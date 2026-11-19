from django.contrib import admin

from databases.models import Backup, Database, DatabaseLink

admin.site.register(Database)
admin.site.register(DatabaseLink)
admin.site.register(Backup)
