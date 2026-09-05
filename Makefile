PYTHON ?= python3

.PHONY: test verify

test:
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v
	$(PYTHON) -m unittest discover -s kernel/linux-6.12.108-zramfix1/tests -p 'test_*.py' -v
	$(PYTHON) -m unittest discover -s bootloader/u-boot-eaidk310/tests -p 'test_*.py' -v
	$(PYTHON) -m unittest discover -s tools -p 'test_*.py' -v

verify:
	$(PYTHON) tools/publication_gate.py .
	find kernel bootloader -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n
