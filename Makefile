all:

build-inplace:
	python3 setup.py build_ext -i

coverage: build-inplace
	python3 -m coverage run -m unittest tests.test_suite

coverage-html:
	python3 -m coverage html

check:: style

style:
	ruff check .

fix:
	ruff check --fix .
	cargo fmt --all

format:
	ruff format .
	cargo fmt --all

check:: typing

typing: build-inplace
	mypy silver_platter/

check:: testsuite

testsuite: build-inplace
	python3 setup.py test
