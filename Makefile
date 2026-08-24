.PHONY: test
test:
	python3 -m unittest discover -s tests -v

.PHONY: lint
lint:
	python3 -m compileall -q askgpt bin/askgpt
