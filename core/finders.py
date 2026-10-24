import os

from django.contrib.staticfiles import finders


class FileSystemFinder(finders.FileSystemFinder):
    """
    Django's FileSystemFinder, but a STATICFILES_DIRS prefix with a slash in it (such as
    "nagar-seva/css") also matches on Windows. Django joins the prefix with os.sep ("\\") while
    {% static %} and runserver ask for "nagar-seva/css/style.css", so it never matched there.
    """

    def find_location(self, root, path, prefix=None):
        if prefix:
            prefix, path = os.path.normpath(prefix), os.path.normpath(path)
        return super().find_location(root, path, prefix)
