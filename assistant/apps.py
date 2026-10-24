from django.apps import AppConfig


class AssistantConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'assistant'
    verbose_name = 'Citizen help assistant'

    def ready(self):
        from . import checks  # noqa: F401  (registers the deploy checks)
