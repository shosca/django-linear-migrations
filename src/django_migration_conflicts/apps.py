import pkgutil
from importlib import import_module
from pathlib import Path

from django.apps import AppConfig, apps
from django.core.checks import Tags, Error, register
from django.db.migrations.loader import MigrationLoader
from django.utils.functional import cached_property

from django_migration_conflicts.compat import is_namespace_module


class DjangoMigrationConflictsAppConfig(AppConfig):
    name = "django_migration_conflicts"
    verbose_name = "django-migration-conflicts"

    def ready(self):
        register(Tags.models)(check_max_migration_files)


def first_party_app_configs():
    for app_config in apps.get_app_configs():
        # Skip apps that seem to be in virtualenvs
        path = Path(app_config.path)
        if "site-packages" in path.parts:
            continue

        yield app_config


class MigrationDetails:
    def __init__(self, app_label):
        self.app_label = app_label

        # Some logic duplicated from MigrationLoader.load_disk, but avoiding
        # loading all migrations since that's relatively slow.
        migrations_module_name, _explicit = MigrationLoader.migrations_module(app_label)
        try:
            migrations_module = import_module(migrations_module_name)
        except ModuleNotFoundError:
            # Unmigrated app
            migrations_module = None
        else:
            # Django ignores namespace migrations modules
            if is_namespace_module(migrations_module):
                migrations_module = None
            # Django ignores non-package migrations modules
            if not hasattr(migrations_module, "__path__"):
                migrations_module = None
        self.migrations_module = migrations_module

    @property
    def has_migrations(self):
        return self.migrations_module is not None

    @cached_property
    def dir(self):
        return Path(self.migrations_module.__file__).parent

    @cached_property
    def names(self):
        return {
            name
            for _, name, is_pkg in pkgutil.iter_modules(self.migrations_module.__path__)
            if not is_pkg and name[0] not in "_~"
        }


dmc_E001_msg = "{app_config.label}'s max_migration.txt does not exist."
dmc_E001_hint = (
    "If you just installed django-migration-conflicts, run 'python manage.py"
    + " makemigrations --initialize-max-migrations'. Otherwise, check how it"
    + " has gone missing."
)

dmc_E002_msg = "{app_config.label}'s max_migration.txt contains multiple lines."
dmc_E002_hint = (
    "This may be the result of a git merge. Fix the file to contain only the"
    + " name of the latest migration."
)

dmc_E003_msg = (
    "{app_config.label}'s max_migration.txt points to non-existent migration"
    + " '{max_migration_name}'."
)
dmc_E003_hint = "Edit the max_migration.txt to contain the latest migration's name."

dmc_E004_msg = (
    "{app_config.label}'s max_migration.txt contains '{max_migration_name}',"
    + " but the latest migration is '{real_max_migration_name}'."
)
dmc_E004_hint = (
    "Edit max_migration.txt to contain '{real_max_migration_name}' or rebase"
    + " '{max_migration_name}' to be the latest migration."
)


def check_max_migration_files(*, app_configs=None, **kwargs):
    errors = []
    for app_config in first_party_app_configs():
        # When only checking certain apps, skip the others
        if app_configs is not None and app_config not in app_configs:
            continue
        app_label = app_config.label
        migration_details = MigrationDetails(app_label)

        if not migration_details.has_migrations:
            continue

        max_migration_txt = migration_details.dir / "max_migration.txt"
        if not max_migration_txt.exists():
            errors.append(
                Error(
                    id="dmc.E001",
                    msg=dmc_E001_msg.format(app_config=app_config),
                    hint=dmc_E001_hint,
                )
            )
            continue

        max_migration_txt_lines = max_migration_txt.read_text().strip().splitlines()
        if len(max_migration_txt_lines) > 1:
            errors.append(
                Error(
                    id="dmc.E002",
                    msg=dmc_E002_msg.format(app_config=app_config),
                    hint=dmc_E002_hint,
                )
            )
            continue

        max_migration_name = max_migration_txt_lines[0]
        if max_migration_name not in migration_details.names:
            errors.append(
                Error(
                    id="dmc.E003",
                    msg=dmc_E003_msg.format(
                        app_config=app_config, max_migration_name=max_migration_name
                    ),
                    hint=dmc_E003_hint,
                )
            )
            continue

        real_max_migration_name = max(migration_details.names)
        if max_migration_name != real_max_migration_name:
            errors.append(
                Error(
                    id="dmc.E004",
                    msg=dmc_E004_msg.format(
                        app_config=app_config,
                        max_migration_name=max_migration_name,
                        real_max_migration_name=real_max_migration_name,
                    ),
                    hint=dmc_E004_hint.format(
                        app_config=app_config,
                        max_migration_name=max_migration_name,
                        real_max_migration_name=real_max_migration_name,
                    ),
                )
            )

    return errors
