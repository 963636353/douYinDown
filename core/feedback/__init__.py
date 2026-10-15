"""Reusable feedback submission, independent of the GUI toolkit."""

from .models import FeedbackDraft, FeedbackError, FeedbackLimits, FeedbackReceipt
from .service import FeedbackService

__all__ = ['FeedbackDraft', 'FeedbackError', 'FeedbackLimits', 'FeedbackReceipt', 'FeedbackService']
