// SPDX-License-Identifier: GPL-2.0+
/*
 * EAIDK-310 raw dual-copy boot-state backend (P3.6-B).
 *
 * Persists the OTA boot state in two redundant 512-byte records on fixed,
 * audited raw sectors of the eMMC user area -- no filesystem, no journal,
 * no persistent U-Boot environment.
 *
 * Record layout (little-endian fields, one full 512-byte sector):
 *   off   size  field
 *   0     8    magic  "EA310BS1"
 *   8     1    format_version (1)
 *   9     1    bootcount
 *   10    1    upgrade_available
 *   11    1    candidate_slot (0 = none)
 *   12    4    sequence (u32 LE, strictly increasing, 0xffffffff = refuse)
 *   16    16   candidate_label (NUL-padded)
 *   32    476  reserved (written as zero)
 *   508   4    crc32 (LE, over bytes 0..507; zlib polynomial, u-boot crc32())
 *
 * Selection: a record is usable only when magic, version AND crc32 all
 * match.  Among usable records the highest sequence wins; equal sequences
 * with different content conflict -> both rejected (fail closed).
 *
 * Store: never overwrite the selected copy.  The write always targets the
 * OTHER sector with sequence+1.  A torn or lost write leaves the previous
 * copy intact, so any power-loss outcome degrades at most to "one stale
 * update missing", never to uncounted candidate boots.  If the store
 * cannot be persisted, bootcount_stored stays 0 and the compiled bootcmd
 * policy (upgrade_available=1 AND bootcount_stored=1) stays on stable.
 *
 * Fail-closed matrix (all -> upgrade_available=0, no candidate, no write):
 *   device resolve error / MMC identity mismatch / capacity below minimum
 *   short read / I/O error / both records invalid / both-invalid variants
 *   (bad magic, bad version, bad crc, mixed) / sequence conflict
 *   sequence exhaustion (0xffffffff)
 *
 * Target sectors (P3.6-B audit, see bootloader docs EMMC-RAW-REGION.md):
 *   LBA 0x6400 (25600, 12 MiB + 512 KiB) and LBA 0x7800 (30720, 15 MiB)
 *   inside the 12-16 MiB unpartitioned tail; zero in the factory image,
 *   the rescue TF image and the live board baseline (870A6910...), and
 *   outside every known write window (U-Boot update = 8-12 MiB).
 */

#include <blk.h>
#include <bootcount.h>
#include <dm.h>
#include <dm/uclass.h>
#include <env.h>
#include <mmc.h>
#include <part.h>
#include <u-boot/crc.h>
#include <wdt.h>

#define EA310_BS_MAGIC		"EA310BS1"
#define EA310_BS_VERSION	1
#define EA310_BS_SIZE		512
#define EA310_BS_CRC_OFF	508
#define EA310_BS_SEQ_MAX	0xffffffffu

/* Diagnostic breadcrumb record (LBA CONFIG_SYS_BOOTCOUNT_RAW_LBA_DIAG):
 * written on every load/store outcome so the boot chain state is visible
 * from Linux (eaidk-bootstate) even without a serial console. */
#define EA310_DIAG_MAGIC	"EA310BDG"
#define EA310_DIAG_VERSION	1
#define EA310_DIAG_CRC_OFF	508

/* diag result codes (record byte 10) */
#define EA310_DIAG_DEV_NOT_FOUND	0
#define EA310_DIAG_IDENTITY_MISMATCH	1
#define EA310_DIAG_READ_ERROR		2
#define EA310_DIAG_NO_USABLE_RECORD	3
#define EA310_DIAG_COMMITTED		4
#define EA310_DIAG_ARMED		5
#define EA310_DIAG_STORE_WRITE_ERROR	6
#define EA310_DIAG_STORE_REFUSED	7
#define EA310_DIAG_STORE_OK		8
#define EA310_DIAG_SEQ_EXHAUSTED	9

struct ea310_bs_record {
	u8 raw[EA310_BS_SIZE];
};

static struct blk_desc *ea310_bs_dev;		/* passed identity gate */
static struct blk_desc *ea310_bs_diag_dev;	/* resolved, for diagnostics */
static int ea310_bs_dev_tried;
static u8 upgrade_available = 1;
static int bootcount_store_ok;

/* identity data as seen by the gate, for the diagnostic breadcrumb */
static u32 ea310_diag_manfid;
static char ea310_diag_name[8];
static u64 ea310_diag_capacity;

static u32 le32_at(const u8 *p)
{
	return (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16) |
	       ((u32)p[3] << 24);
}

static void put_le32(u8 *p, u32 v)
{
	p[0] = v & 0xff;
	p[1] = (v >> 8) & 0xff;
	p[2] = (v >> 16) & 0xff;
	p[3] = (v >> 24) & 0xff;
}

/*
 * Best-effort diagnostic breadcrumb.  Records what the backend saw this
 * boot: device resolution, identity check data, selection result and the
 * store outcome.  Never influences boot decisions; a failed diag write is
 * silently ignored.
 */
static void ea310_diag_write(int stage, int result, int dev_present,
			     u32 manfid, const char *name, u64 capacity,
			     u32 seq, int selected, u8 upg, u8 bootcnt,
			     int stored)
{
#ifdef CONFIG_SYS_BOOTCOUNT_RAW_DIAG
	struct blk_desc *diag_dev = ea310_bs_diag_dev;
	u8 buf[EA310_BS_SIZE];

	if (!diag_dev)
		return;

	memset(buf, 0, sizeof(buf));
	memcpy(buf, EA310_DIAG_MAGIC, 8);
	buf[8] = EA310_DIAG_VERSION;
	buf[9] = (u8)stage;
	buf[10] = (u8)result;
	buf[11] = (u8)(dev_present ? 1 : 0);
	put_le32(buf + 12, manfid);
	if (name)
		memcpy(buf + 16, name, strnlen(name, 8));
	put_le32(buf + 24, (u32)(capacity & 0xffffffffu));
	put_le32(buf + 28, (u32)(capacity >> 32));
	put_le32(buf + 32, seq);
	buf[36] = (u8)(selected & 0xff);
	buf[37] = upg;
	buf[38] = bootcnt;
	buf[39] = (u8)(stored ? 1 : 0);
	put_le32(buf + EA310_DIAG_CRC_OFF,
		 crc32(0, buf, EA310_DIAG_CRC_OFF));

	blk_dwrite(diag_dev, CONFIG_SYS_BOOTCOUNT_RAW_LBA_DIAG, 1, buf);
#else
	(void)stage; (void)result; (void)dev_present; (void)manfid;
	(void)name; (void)capacity; (void)seq; (void)selected;
	(void)upg; (void)bootcnt; (void)stored;
#endif
}

static int record_valid(const struct ea310_bs_record *r)
{
	u32 crc;

	if (memcmp(r->raw, EA310_BS_MAGIC, 8) != 0)
		return 0;
	if (r->raw[8] != EA310_BS_VERSION)
		return 0;
	crc = crc32(0, r->raw, EA310_BS_CRC_OFF);
	return crc == le32_at(r->raw + EA310_BS_CRC_OFF);
}

/*
 * Resolve and identity-check the state device.  Returns NULL on ANY doubt;
 * every caller treats NULL as fail-closed (stable boot, no writes).
 *
 * Device lookup uses the DM-native name-based API (NOT the legacy
 * blk_get_device_by_str registry, which is empty in DM-only builds and
 * would make every lookup fail closed).
 */
static struct blk_desc *ea310_bs_get_dev(void)
{
	struct blk_desc *desc = NULL;
	struct mmc *mmc;
	int devnum;

	if (ea310_bs_dev_tried)
		return ea310_bs_dev;
	ea310_bs_dev_tried = 1;

	devnum = (int)dectoul(CONFIG_SYS_BOOTCOUNT_RAW_DEVID, NULL);
	desc = blk_get_devnum_by_uclass_idname(
		CONFIG_SYS_BOOTCOUNT_RAW_INTERFACE, devnum);
	if (!desc) {
		puts("bootcount-raw: device not found\n");
		return NULL;
	}
	ea310_bs_diag_dev = desc;

	/* Identity sanity is enforced only on real MMC hardware; the
	 * sandbox "host" interface has no CID and is exempt. */
	if (strcmp(CONFIG_SYS_BOOTCOUNT_RAW_INTERFACE, "mmc") == 0) {
		mmc = find_mmc_device(desc->devnum);
		if (!mmc) {
			puts("bootcount-raw: mmc device missing\n");
			desc = NULL;
		} else if (!mmc->has_init && mmc_init(mmc)) {
			puts("bootcount-raw: mmc init failed\n");
			desc = NULL;
		} else {
			u8 manfid = (mmc->cid[0] >> 24) & 0xff;
			char name[7];

			/* CID bytes 3..8 (big-endian dword pair) = product name */
			name[0] = (mmc->cid[0] >> 0) & 0xff;
			name[1] = (mmc->cid[1] >> 24) & 0xff;
			name[2] = (mmc->cid[1] >> 16) & 0xff;
			name[3] = (mmc->cid[1] >> 8) & 0xff;
			name[4] = (mmc->cid[1] >> 0) & 0xff;
			name[5] = (mmc->cid[2] >> 24) & 0xff;
			name[6] = '\0';
			ea310_diag_manfid = manfid;
			memcpy(ea310_diag_name, name, sizeof(name));
			ea310_diag_capacity = mmc->capacity;
			if (manfid != CONFIG_SYS_BOOTCOUNT_RAW_MMC_MANFID ||
			    strncmp(name, CONFIG_SYS_BOOTCOUNT_RAW_MMC_NAME,
				    strlen(CONFIG_SYS_BOOTCOUNT_RAW_MMC_NAME)) != 0 ||
			    strlen(CONFIG_SYS_BOOTCOUNT_RAW_MMC_NAME) != 6 ||
			    mmc->capacity < CONFIG_SYS_BOOTCOUNT_RAW_MMC_MINSIZE) {
				printf("bootcount-raw: identity mismatch "
				       "(manfid=%02x name=%s cap=%llu)\n",
				       manfid, name,
				       (unsigned long long)mmc->capacity);
				desc = NULL;
			}
		}
	}

	ea310_bs_dev = desc;
	return desc;
}

struct ea310_bs_sel {
	int usable[2];
	u32 seq[2];
	u8 bootcount;
	u8 upgrade_available;
	u8 slot;
	char label[17];
	int selected;		/* 0/1 usable with highest seq, -1 none */
};

static void ea310_bs_select(struct ea310_bs_sel *s,
			    const struct ea310_bs_record recs[2])
{
	int i;

	memset(s, 0, sizeof(*s));
	s->selected = -1;
	for (i = 0; i < 2; i++) {
		s->usable[i] = record_valid(&recs[i]);
		if (s->usable[i])
			s->seq[i] = le32_at(recs[i].raw + 12);
	}
	if (!s->usable[0] && !s->usable[1])
		return;			/* fail closed: none usable */

	for (i = 0; i < 2; i++) {
		if (!s->usable[i])
			continue;
		if (s->selected < 0 || (int)(s->seq[i] - s->seq[s->selected]) > 0)
			s->selected = i;
	}
	/* equal-sequence conflict with different payload -> reject both */
	if (s->usable[0] && s->usable[1] &&
	    s->seq[0] == s->seq[1] &&
	    memcmp(recs[0].raw, recs[1].raw, EA310_BS_CRC_OFF) != 0) {
		puts("bootcount-raw: sequence conflict\n");
		s->selected = -1;
		return;
	}
	s->bootcount = recs[s->selected].raw[9];
	s->upgrade_available = recs[s->selected].raw[10];
	s->slot = recs[s->selected].raw[11];
	memcpy(s->label, recs[s->selected].raw + 16, 16);
	s->label[16] = '\0';
}

ulong bootcount_load(void)
{
	struct blk_desc *desc = ea310_bs_get_dev();
	struct ea310_bs_record recs[2];
	struct ea310_bs_sel sel;
	lbaint_t lbas[2];
	int ret = 0;
	u32 diag_seq = 0xffffffffu;
	int diag_sel = -1;

	upgrade_available = 0;
	if (!desc) {
		env_set_ulong("upgrade_available", upgrade_available);
		env_set_ulong("bootcount_stored", 0);
		ea310_diag_write(0,
				 ea310_bs_diag_dev ?
				 EA310_DIAG_IDENTITY_MISMATCH :
				 EA310_DIAG_DEV_NOT_FOUND,
				 ea310_bs_diag_dev != NULL,
				 ea310_diag_manfid, ea310_diag_name,
				 ea310_diag_capacity, diag_seq, diag_sel,
				 0, 0, 0);
		return 0;
	}

	lbas[0] = CONFIG_SYS_BOOTCOUNT_RAW_LBA_A;
	lbas[1] = CONFIG_SYS_BOOTCOUNT_RAW_LBA_B;
	if (blk_dread(desc, lbas[0], 1, recs[0].raw) != 1 ||
	    blk_dread(desc, lbas[1], 1, recs[1].raw) != 1) {
		puts("bootcount-raw: read error\n");
		env_set_ulong("upgrade_available", upgrade_available);
		env_set_ulong("bootcount_stored", 0);
		ea310_diag_write(0, EA310_DIAG_READ_ERROR, 1,
				 ea310_diag_manfid, ea310_diag_name,
				 ea310_diag_capacity, diag_seq, diag_sel,
				 0, 0, 0);
		return 0;
	}

	ea310_bs_select(&sel, recs);
	if (sel.selected >= 0) {
		diag_seq = sel.seq[sel.selected];
		diag_sel = sel.selected;
	}
	if (sel.selected >= 0 && sel.upgrade_available) {
		upgrade_available = 1;
		ret = sel.bootcount;
	}

	/*
	 * Export the trial state for the bootcmd script.  Every abnormal
	 * case above left upgrade_available at 0, so bootcmd falls back to
	 * the standard bootflow scan (stable).  Fail closed, never open.
	 */
	env_set_ulong("upgrade_available", upgrade_available);
	ea310_diag_write(0,
			 upgrade_available ? EA310_DIAG_ARMED :
			  (sel.selected >= 0 ? EA310_DIAG_COMMITTED :
			   EA310_DIAG_NO_USABLE_RECORD),
			 1, ea310_diag_manfid, ea310_diag_name,
			 ea310_diag_capacity, diag_seq, diag_sel,
			 upgrade_available, (u8)ret,
			 bootcount_store_ok);
	return ret;
}

void bootcount_store(ulong a)
{
	struct blk_desc *desc;
	struct ea310_bs_record recs[2];
	struct ea310_bs_sel sel;
	lbaint_t target;
	u32 seq;

	bootcount_store_ok = 0;
	env_set_ulong("bootcount_stored", bootcount_store_ok);

	/* Only persist during an armed upgrade; upgrade_available is 0
	 * whenever load failed or saw a committed record. */
	if (!upgrade_available)
		return;

	desc = ea310_bs_get_dev();
	if (!desc)
		return;

	if (blk_dread(desc, CONFIG_SYS_BOOTCOUNT_RAW_LBA_A, 1, recs[0].raw) != 1 ||
	    blk_dread(desc, CONFIG_SYS_BOOTCOUNT_RAW_LBA_B, 1, recs[1].raw) != 1) {
		puts("bootcount-raw: read error before store\n");
		ea310_diag_write(1, EA310_DIAG_READ_ERROR, 1,
				 ea310_diag_manfid, ea310_diag_name,
				 ea310_diag_capacity, 0xffffffffu, -1,
				 upgrade_available, (u8)a, 0);
		return;
	}
	ea310_bs_select(&sel, recs);
	if (sel.selected < 0) {
		puts("bootcount-raw: no usable record, refusing store\n");
		ea310_diag_write(1, EA310_DIAG_STORE_REFUSED, 1,
				 ea310_diag_manfid, ea310_diag_name,
				 ea310_diag_capacity, 0xffffffffu, -1,
				 upgrade_available, (u8)a, 0);
		return;
	}
	seq = sel.seq[sel.selected];
	if (seq == EA310_BS_SEQ_MAX) {
		puts("bootcount-raw: sequence exhausted\n");
		ea310_diag_write(1, EA310_DIAG_SEQ_EXHAUSTED, 1,
				 ea310_diag_manfid, ea310_diag_name,
				 ea310_diag_capacity, seq, sel.selected,
				 upgrade_available, (u8)a, 0);
		return;
	}

	/* write the OTHER copy: prefer the unusable one, else lower seq */
	if (!sel.usable[0])
		target = CONFIG_SYS_BOOTCOUNT_RAW_LBA_A;
	else if (!sel.usable[1])
		target = CONFIG_SYS_BOOTCOUNT_RAW_LBA_B;
	else if (sel.selected == 0)
		target = CONFIG_SYS_BOOTCOUNT_RAW_LBA_B;
	else
		target = CONFIG_SYS_BOOTCOUNT_RAW_LBA_A;

	{
		struct ea310_bs_record fresh;

		memset(&fresh, 0, sizeof(fresh));
		memcpy(fresh.raw, EA310_BS_MAGIC, 8);
		fresh.raw[8] = EA310_BS_VERSION;
		fresh.raw[9] = (u8)(a & 0xff);
		fresh.raw[10] = upgrade_available;
		fresh.raw[11] = sel.slot;
		put_le32(fresh.raw + 12, seq + 1);
		memcpy(fresh.raw + 16, sel.label, 16);
		put_le32(fresh.raw + EA310_BS_CRC_OFF,
			 crc32(0, fresh.raw, EA310_BS_CRC_OFF));

		if (blk_dwrite(desc, target, 1, fresh.raw) != 1) {
			puts("bootcount-raw: write error\n");
			ea310_diag_write(1, EA310_DIAG_STORE_WRITE_ERROR, 1,
					 ea310_diag_manfid, ea310_diag_name,
					 ea310_diag_capacity, seq,
					 sel.selected, upgrade_available,
					 (u8)a, 0);
			return;
		}
	}

	bootcount_store_ok = 1;
	env_set_ulong("bootcount_stored", bootcount_store_ok);

	/*
	 * Trial watchdog (P3.7): the increment that lands within the
	 * bootlimit IS the candidate trial boot.  Start the hardware
	 * watchdog now so any hang after this point (U-Boot load, kernel,
	 * initramfs, userspace until the trial feed service takes over)
	 * produces a SoC reset; the next boot then exceeds the bootlimit
	 * and altbootcmd rolls back to the stable entry.  Rollback boots
	 * (increment exceeding the bootlimit) must NOT start the watchdog.
	 * Best effort: a missing or broken WDT is reported but never blocks
	 * the boot, and the bootcount/stored-state remains the sole
	 * rollback decision input.
	 */
	if (env_get_ulong("upgrade_available", 10, 0) == 1) {
		unsigned long bootlimit = env_get_ulong("bootlimit", 10, 0);
		unsigned long bc_new = a & 0xff;	/* the new bootcount value */
		if (bootlimit > 0 && bc_new <= bootlimit) {
			struct udevice *wdt_dev = NULL;
			int rc;

			/* uclass_first_device() probes on demand (void in
			 * v2024.07; probe failures leave *devp NULL); the
			 * board and the sandbox each expose one WDT. */
			uclass_first_device(UCLASS_WDT, &wdt_dev);
			if (!wdt_dev) {
				puts("bootcount-raw: no watchdog device\n");
				env_set_ulong("trial_wdt", 0);
			} else {
				rc = wdt_start(wdt_dev,
					CONFIG_SYS_BOOTCOUNT_TRIAL_WDT_TIMEOUT_MS,
					0);
				printf("bootcount-raw: trial watchdog via %s (%u ms) rc=%d\n",
				       wdt_dev->name,
				       (unsigned)CONFIG_SYS_BOOTCOUNT_TRIAL_WDT_TIMEOUT_MS,
				       rc);
				env_set_ulong("trial_wdt", rc == 0 ? 1 : 0);
			}
		}
	}

	ea310_diag_write(1, EA310_DIAG_STORE_OK, 1,
			 ea310_diag_manfid, ea310_diag_name,
			 ea310_diag_capacity, seq + 1,
			 sel.selected, upgrade_available, (u8)a, 1);
}
