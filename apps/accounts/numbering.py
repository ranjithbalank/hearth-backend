from django.db import transaction
from django.utils import timezone


#: GST Rule 46(b) caps a tax-invoice number at 16 characters. Series that are
#: handed to a customer as a tax document pass this; internal paperwork
#: (PO/GRN/BEO) has no such limit and leaves it None.
GST_MAX_DOC_NUMBER = 16


def next_document_number(model, field, prefix, seq_width=5, max_total=None):
    """PREFIX-YYYYMM-NNNNN, counter resetting each calendar month.

    Locks the singleton Property row to serialize generation (single-property
    release) and avoid races between concurrent settles/creates.

    `max_total` caps the finished string, trimming the prefix to fit. The
    statutory series pass GST_MAX_DOC_NUMBER: the format is len(prefix)+13, so
    anything past a 3-character prefix breaches Rule 46(b) — and the shipped
    default POS prefix was "BILL", which produced a 17-character number.
    Trimming at generation keeps every already-configured property compliant
    without a data migration; the API separately refuses new prefixes that
    would need trimming, so this is a backstop rather than the main guard.
    """
    from .models import Property
    with transaction.atomic():
        Property.objects.select_for_update().first()
        period = timezone.localdate().strftime("%Y%m")
        if max_total:
            # Overhead is the two separators plus the period and the counter.
            prefix = prefix[: max(1, max_total - (8 + seq_width))]
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
