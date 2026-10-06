PYTHON ?= python3

.PHONY: test verify verify-custom-source release release-verify reproduce

test:
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v
	$(PYTHON) -m unittest discover -s kernel/linux-6.12.108-zramfix1/tests -p 'test_*.py' -v
	$(PYTHON) -m unittest discover -s bootloader/u-boot-eaidk310/tests -p 'test_*.py' -v
	$(PYTHON) -m unittest discover -s tools -p 'test_*.py' -v

verify-custom-source:
	$(PYTHON) tools/verify_custom_source.py --check

verify:
	$(PYTHON) tools/publication_gate.py .
	$(PYTHON) tools/verify_custom_source.py --check
	find kernel bootloader -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n

# Release engineering (P7).  These targets drive real kernel builds and must
# run on Linux (WSL ext4 from a Windows host):
#   wsl.exe -u root -e bash -c "cd <repo> && make release VERSION=6.18.54"
# `make test` / `make verify` stay repository-only and never build a kernel.
release:
	./tools/release-kernel.sh $(VERSION) --clean

release-verify:
	./tools/release-kernel.sh $(VERSION) --verify --resume

reproduce:
	./tools/release-kernel.sh $(VERSION) --clean && \
	./tools/release-kernel.sh $(VERSION) --verify --resume
