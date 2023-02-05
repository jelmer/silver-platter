all:

coverage:
	python3 -m coverage run -m unittest tests.test_suite

coverage-html:
	python3 -m coverage html

check:: style

style:
	flake8

check:: typing

typing:
	mypy silver_platter/

check:: testsuite

testsuite:
	python3 setup.py test

%_pb2.py: %.proto
	protoc --python_out=. $<
