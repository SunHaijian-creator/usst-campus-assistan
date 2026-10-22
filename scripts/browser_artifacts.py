"""截图仅对账号显示名做临时匿名处理，不改应用或数据库中的账号。"""


def save_screenshot(page, path):
    identity = page.locator("#identity")
    original = identity.text_content() if identity.count() else None
    try:
        if original:
            identity.evaluate("element => { element.textContent = '演示账号（已匿名）'; }")
        page.screenshot(path=str(path), full_page=True)
    finally:
        if original:
            identity.evaluate("(element, name) => { element.textContent = name; }", original)
