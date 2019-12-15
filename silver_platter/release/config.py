from google.protobuf import text_format

from . import config_pb2


def read_config(f):
    return text_format.Parse(f.read(), config_pb2.Config())


def find_project(config, name):
    for project in config.project:
        if project.name == name:
            return project
    return None
