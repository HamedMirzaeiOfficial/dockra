from django.contrib import admin

from accounts.models import ApiToken, Membership, Organization

admin.site.register(Organization)
admin.site.register(Membership)
admin.site.register(ApiToken)
