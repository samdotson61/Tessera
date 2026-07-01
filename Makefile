# Tessera — common tasks. (Windows users: run the python commands directly.)
PY ?= python

.PHONY: test regression demo serve report export clean

test:
	$(PY) -m unittest discover -s tests -t . -v

regression:   # the coverage@precision harness gate on the fixed gold sets
	$(PY) -m unittest tests.test_regression -v

demo:
	$(PY) -m tessera --db demo.db demo --target 0.95

serve:
	$(PY) -m tessera --db demo.db demo --serve

report:
	$(PY) -m tessera --db demo.db report --dataset demo

export:
	$(PY) -m tessera --db demo.db export --dataset demo --out labels.jsonl --pairs pairs.jsonl

clean:
	rm -f *.db labels.jsonl pairs.jsonl
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
