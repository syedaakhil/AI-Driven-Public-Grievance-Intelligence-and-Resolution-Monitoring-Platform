"""
Training on collected datasets. TensorFlow is optional (requirements-ml.txt): it is imported only
inside train.py, so the website, the tests and Vercel run without it.
"""
import importlib.util


def tensorflow_available():
    return importlib.util.find_spec('tensorflow') is not None
