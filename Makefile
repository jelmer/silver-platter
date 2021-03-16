all:

check:
	flake8
	mypy silver_platter/
	python3 setup.py test

typing:
	mypy silver_platter/

style:
	flake8

%_pb2.py: %.proto
	protoc --python_out=. $<
