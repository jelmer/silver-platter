all: silver_platter/release/config_pb2.py

check:
	flake8
	python3 setup.py test

%_pb2.py: %.proto
	protoc --python_out=. $<
