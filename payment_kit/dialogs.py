from qfluentwidgets import MessageBox


def info(parent, title, content):
    box = MessageBox(str(title), str(content), parent)
    box.yesButton.setText("确定")
    box.hideCancelButton()
    return bool(box.exec())


def ask_yes_no(parent, title, content, yes_text="去购买", no_text="取消"):
    box = MessageBox(str(title), str(content), parent)
    box.yesButton.setText(yes_text)
    box.cancelButton.setText(no_text)
    return bool(box.exec())
