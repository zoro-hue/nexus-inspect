import pickle
import yaml


def load_session(request):
    return pickle.loads(request.data)


def load_config(text):
    return yaml.load(text)


def load_config_safely(text):
    return yaml.load(text, Loader=yaml.SafeLoader)
