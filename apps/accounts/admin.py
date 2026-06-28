from django.contrib import admin

from .models import AuditLog, Entitlement, Property, User

admin.site.register(User)
admin.site.register(Property)
admin.site.register(Entitlement)
admin.site.register(AuditLog)
