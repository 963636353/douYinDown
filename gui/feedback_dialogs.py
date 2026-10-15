"""Small presentation-only feedback dialogs."""

from qfluentwidgets import BodyLabel, MessageBoxBase


class FeedbackSuccessDialog(MessageBoxBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.viewLayout.addWidget(BodyLabel('反馈已收到，感谢你的反馈！', self))
        self.yesButton.setText('知道了')
        self.hideCancelButton()
        self.widget.setMinimumWidth(420)
