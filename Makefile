all:

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
