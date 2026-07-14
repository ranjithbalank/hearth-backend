from django.db import transaction
from django.utils import timezone


def next_document_number(model, field, prefix, seq_width=5):
    """PREFIX-YYYYMM-NNNNN, counter resetting each calendar month.

    Locks the singleton Property row to serialize generation (single-property
    release) and avoid races between concurrent settles/creates.
    """
    from .models import Property
    with transaction.atomic():
        Property.objects.select_for_update().first()
        period = timezone.localdate().strftime("%Y%m")
        stub = f"{prefix}-{period}-"
        last = (model.objects.filter(**{f"{field}__startswith": stub})
                .order_by(f"-{field}").first())
        seq = 1
        if last:
            try:
                seq = int(getattr(last, field).rsplit("-", 1)[-1]) + 1
            except ValueError:
                seq = model.objects.filter(**{f"{field}__startswith": stub}).count() + 1
        return f"{stub}{seq:0{seq_width}d}"
