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
from .rbac import PROTECTED, can_access


def active_entitlements():
    prop = Property.objects.select_related("entitlement").first()
    if prop and hasattr(prop, "entitlement"):
        return prop.entitlement.as_dict()
    # No setup yet — default everything on so setup/login flows work.
    return {"hms": True, "restaurant": True, "banquets": True, "rms": True}


def user_branch_ids(user):
    """Which Branch ids this login may operate in today.

    Returns "*" for Super Admin/MD/GM (implicit all-branch access, same as
    their existing module "*" access) or a set of Branch ids drawn from their
    active UserBranchAccess rows (respecting start_date/end_date loans).
    """
    if getattr(user, "role", None) in PROTECTED:
        return "*"
    # Single-property mode: with no branches configured there is nothing to
    # scope BY — strict scoping would blank every floor screen (tables, rooms,
    # bar) for every non-executive role (QA finding TC-048/065: the cashier's
    # POS floor was empty on upgraded installs that predate branches).
    from .models import Branch
    if not Branch.objects.exists():
        return "*"
    from datetime import date

    today = date.today()
    return {
        a.branch_id for a in user.branch_access.select_related("branch").all()
        if a.is_active_on(today)
    }


def resolve_active_branch(request):
    """The branch the client asked to operate in, if any and if allowed.

    Read from the `X-Branch-Id` header (the frontend's branch switcher sends
    this). Returns None when the header is absent, not a valid int, or the
    user has no access to it — callers treat None as "don't filter" for
    all-branch roles, or "no branch selected yet" otherwise.
    """
    raw = request.headers.get("X-Branch-Id")
    if not raw:
        return None
    try:
        branch_id = int(raw)
    except (TypeError, ValueError):
        return None
    allowed = user_branch_ids(request.user)
    if allowed != "*" and branch_id not in allowed:
        return None
    return branch_id


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


def visible_branch_ids(request):
    """Resolve which branch id(s) this request should see: "*" for every
    branch (all-branch role with no specific header), or a concrete set
    (one branch if X-Branch-Id was sent and allowed, otherwise every branch
    the caller is assigned to)."""
    allowed = user_branch_ids(request.user)
    branch_id = resolve_active_branch(request)
    if branch_id is not None:
        return {branch_id}
    return allowed


class BranchScopedMixin:
    """Filter a viewset's queryset to the caller's active branch — strictly:
    every row must belong to that branch (see Room/Table/BarTable, where a
    table always belongs to exactly one location).

    Mix in on top of ModuleViewSetMixin/AnyModuleViewSetMixin. The model must
    have a `location` FK to `accounts.Branch`. If the caller has all-branch
    access (Super Admin/MD/GM) and sends no X-Branch-Id, every branch's rows
    are returned — that's the group-oversight view, not a bug. A
    branch-restricted user with no valid X-Branch-Id sees nothing, rather
    than silently leaking every branch's data.

    For a model where a blank `location` means "shared by every branch"
    instead (see Category/MenuItem), don't use this mixin — filter directly
    with `visible_branch_ids()` and OR in the NULL-location rows.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        visible = visible_branch_ids(self.request)
        if visible == "*":
            return qs
        return qs.filter(location_id__in=visible) if visible else qs.none()


def requester_branch(request):
    """Which branch a row being created right now should be tagged with:
    the active branch header if sent, else the caller's own branch when
    they're only ever assigned to one — so a single-branch login never has
    to pick it explicitly. None for all-branch roles with no header (their
    rows stay shared/untagged) — used by POS orders, indents, POs,
    reservations."""
    branch_id = resolve_active_branch(request)
    if branch_id is not None:
        return branch_id
    visible = user_branch_ids(request.user)
    if isinstance(visible, set) and len(visible) == 1:
        return next(iter(visible))
    return None


def shared_or_visible(qs, request, field="location"):
    """For a model where a blank `location` means shared by every branch
    (see Category/MenuItem/Ingredient) rather than exclusive to one: a
    scoped caller sees "mine + shared", never another branch's exclusives.
    An unscoped caller (all-branch role, or nobody's assigned branches yet)
    still sees everything — unchanged from before this feature existed.

    `field` lets a model with no `location` of its own scope via a related
    one instead — e.g. GoodsReceipt has none, but its PO does:
    `shared_or_visible(qs, request, field="purchase_order__location")`.
    """
    from django.db.models import Q
    visible = visible_branch_ids(request)
    if visible == "*":
        return qs
    return qs.filter(Q(**{f"{field}__isnull": True}) | Q(**{f"{field}_id__in": visible}))


class BranchUniqueFriendlyMixin:
    """Turn the DB-level per-location uniqueness constraint's IntegrityError
    into a clean 400 instead of a 500.

    Pairs with `validators = []` on the serializer (see RoomSerializer):
    DRF force-requires every field in a UniqueConstraint — including a
    conditional one it can't actually reason about — which would wrongly
    demand `location` on every create. Skipping DRF's auto-validator and
    enforcing uniqueness at the DB level instead needs this to still fail
    politely on a genuine duplicate.
    """

    duplicate_message = "That name already exists there."

    def _save_or_400(self, serializer):
        from django.db import IntegrityError
        from rest_framework.exceptions import ValidationError
        try:
            serializer.save()
        except IntegrityError:
            raise ValidationError({"detail": self.duplicate_message})

    def perform_create(self, serializer):
        self._save_or_400(serializer)

    def perform_update(self, serializer):
        self._save_or_400(serializer)
