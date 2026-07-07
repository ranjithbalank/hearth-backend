"""Deny-by-default DRF permissions keyed to role allow-lists + edition entitlements.

Usage on a viewset:

    class FrontDeskViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
        module = "frontdesk"
        ...

The mixin attaches ModulePermission which checks BOTH that the user's role may access
the module AND that the active property's entitlement enables it (BRD SR-021, NFR-015).
"""
from rest_framework.permissions import BasePermission

from .constants import entitlement_allows
from .models import Property
from .rbac import can_access


def active_entitlements():
    prop = Property.objects.select_related("entitlement").first()
    if prop and hasattr(prop, "entitlement"):
        return prop.entitlement.as_dict()
    # No setup yet — default everything on so setup/login flows work.
    return {"hms": True, "restaurant": True, "banquets": True, "rms": True}


class ModulePermission(BasePermission):
    message = "You do not have access to this module."

    def has_permission(self, request, view):
        module = getattr(view, "module", None)
        if module is None:
            return True
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if not can_access(user.role, module):
            self.message = f"Role '{user.role}' cannot access '{module}'."
            return False
        if not entitlement_allows(active_entitlements(), module):
            self.message = f"Module '{module}' is not enabled for this property."
            return False
        return True


class ModuleViewSetMixin:
    """Mix into any viewset and set `module = '<key>'`."""

    permission_classes = [ModulePermission]
    module = None


class AnyModulePermission(BasePermission):
    """Like ModulePermission, but passes if the role/entitlement combo allows
    ANY of `view.modules` — for shared endpoints (Orders, menu, tables) that
    both the restaurant floor ("pos") and the bar ("barpos") use. Which
    specific rows a role may act on is then narrowed further by the
    viewset's own queryset scoping, not by this permission."""

    message = "You do not have access to this module."

    def has_permission(self, request, view):
        modules = getattr(view, "modules", None) or []
        if not modules:
            return True
        user = request.user
        if not (user and user.is_authenticated):
            return False
        ent = active_entitlements()
        for module in modules:
            if can_access(user.role, module) and entitlement_allows(ent, module):
                return True
        self.message = f"Role '{user.role}' cannot access any of {modules}."
        return False


class AnyModuleViewSetMixin:
    """Mix into any viewset and set `modules = ['<key1>', '<key2>']`."""

    permission_classes = [AnyModulePermission]
    modules = []
