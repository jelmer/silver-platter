all: build-inplace

build-inplace:
	python3 setup.py build_ext -i
	python3 setup.py build_rust -i

coverage: build-inplace
	python3 -m coverage run -m unittest tests.test_suite

coverage-html:
	python3 -m coverage html

check:: style

style:
	PYTHONPATH=$(shell pwd)/py ruff check py

fix:
	ruff check --fix .
	cargo fmt --all

format:
	ruff format .
	cargo fmt --all

check:: testsuite

testsuite: build-inplace
	python3 setup.py test
