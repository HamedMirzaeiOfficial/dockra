from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError

from apps_core.models import App, CronJob, Deployment, Disk, Domain, EnvVar, Redirect

admin.site.register(App)
admin.site.register(Deployment)
admin.site.register(EnvVar)
admin.site.register(Domain)
admin.site.register(Disk)
admin.site.register(CronJob)


class RedirectForm(forms.ModelForm):
    class Meta:
        model = Redirect
        exclude = ["created_at"]

    def clean(self):
        from apps_core.validation import RedirectError, check_redirect

        data = super().clean()
        app = data.get("app")
        if app is None:
            return data
        try:
            data.update(check_redirect(app, data, redirect=self.instance if self.instance.pk else None))
        except RedirectError as exc:
            raise ValidationError(str(exc)) from exc
        return data


@admin.register(Redirect)
class RedirectAdmin(admin.ModelAdmin):
    """Changes made from the Django admin must reach Traefik too, not just the database."""

    form = RedirectForm
    list_display = ["app", "source_host", "source_path", "match", "target", "keep_path", "permanent"]
    list_filter = ["app", "match", "permanent"]
    search_fields = ["source_host", "source_path", "target", "app__slug"]

    def _sync(self, request, apps):
        from engine.actions import sync_app

        for app in {item.id: item for item in apps}.values():
            sync_app(app, request.user)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        self._sync(request, [obj.app])

    def delete_model(self, request, obj):
        app = obj.app
        super().delete_model(request, obj)
        self._sync(request, [app])

    def delete_queryset(self, request, queryset):
        apps = [item.app for item in queryset.select_related("app")]
        super().delete_queryset(request, queryset)
        self._sync(request, apps)
