# Генератор QR-кодов для ссылок, текста и контактов.
# Спрашивает тип данных, размер, цвета и форму модулей,
# затем сохраняет qr.png рядом с файлом программы.
# Работает локально, без интернета и сторонних сервисов.

import math
import sys
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw

# Размер картинки по умолчанию (и значение для пустого/неверного ввода).
DEFAULT_SIZE = 300
# Верхний предел размера: больше не нужно и может съесть всю память.
MAX_SIZE = 5000
# Минимальный коэффициент контраста модулей и фона (шкала WCAG: от 1 до 21).
MIN_CONTRAST = 3.0
# Максимальная длина ссылки или текста в символах.
MAX_TEXT_LENGTH = 1000
# Минимум пикселей на модуль: при меньшем размере код не создаём.
MIN_PIXELS_PER_MODULE = 3
# Предел стороны промежуточного холста при отрисовке фигурных форм.
MAX_RENDER_SIDE = 2400
# Количество точек, которыми описывается контур сердечка.
HEART_STEPS = 40

# Русские названия форм -> внутренние имена, понятные коду.
SHAPES = {"квадрат": "square", "круг": "circle", "сердце": "heart"}

# Русские названия цветов -> HEX. Записаны по два варианта: с «ё» и без.
COLOR_NAMES = {
    "чёрный": "#000000",
    "черный": "#000000",
    "белый": "#FFFFFF",
    "красный": "#FF0000",
    "синий": "#0000FF",
    "зелёный": "#008000",
    "зеленый": "#008000",
}
# Подсказка, которая печатается в приглашении ввода цвета.
COLOR_HINT = "чёрный, белый, красный, синий, зелёный или #RRGGBB"


# Спрашивает размер картинки.
# Пустой ввод или не число -> 300 пикселей с пояснением пользователю,
# слишком большое число -> 5000 пикселей.
def ask_size() -> int:
    raw = input(f"Размер картинки в пикселях (Enter - {DEFAULT_SIZE}): ").strip()
    # isdigit отсекает буквы, пробелы и минус, поэтому int() здесь безопасен.
    if raw.isdigit() and int(raw) > 0:
        size = int(raw)
        # Сверху ограничиваем: огромная картинка может занять всю память.
        if size > MAX_SIZE:
            print(f"Размер больше {MAX_SIZE} - использую {MAX_SIZE} пикселей.")
            return MAX_SIZE
        return size

    print(f"Размер не введён или не является числом - использую {DEFAULT_SIZE} пикселей.")
    return DEFAULT_SIZE


# Разбирает цвет: русское название или HEX вида #RRGGBB.
# Возвращает строку #RRGGBB или None, если ввод не распознан.
def parse_color(raw: str) -> str | None:
    value = raw.strip().lower()  # Регистр и лишние пробелы не важны.
    if value in COLOR_NAMES:
        return COLOR_NAMES[value]

    # Решётку разрешаем не писать: и "#ff0000", и "ff0000" считаются цветом.
    if value.startswith("#"):
        value = value[1:]
    # После этого должно остаться ровно 6 шестнадцатеричных цифр.
    if len(value) == 6 and all(ch in "0123456789abcdef" for ch in value):
        return f"#{value.upper()}"
    return None


# Превращает строку #RRGGBB в кортеж (R, G, B).
def hex_to_rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))


# Относительная яркость цвета по формуле WCAG (0 - чёрный, 1 - белый).
def relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        # Сначала снимаем гамма-кривую sRGB: без этого яркость считается неверно.
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    # Веса каналов из стандарта WCAG: глаз наиболее чувствителен к зелёному.
    r, g, b = (channel(value) for value in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# Коэффициент контраста двух цветов: 1 - цвета одинаковы, 21 - чёрный и белый.
def contrast_ratio(first: str, second: str) -> float:
    # Светлый цвет ставим в числитель, тёмный - в знаменатель.
    light = max(relative_luminance(hex_to_rgb(first)), relative_luminance(hex_to_rgb(second)))
    dark = min(relative_luminance(hex_to_rgb(first)), relative_luminance(hex_to_rgb(second)))
    # Слагаемое 0.05 задано стандартом: защищает от деления на ноль.
    return (light + 0.05) / (dark + 0.05)


# vCard 3.0 требует экранировать обратный слэш, точку с запятой,
# запятую и перевод строки внутри значений.
def vcard_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")  # Обратный слэш обрабатываем первым.
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


# Делит введённое имя на фамилию и имя для поля N.
# Последнее слово - фамилия, все предыдущие - имя.
def split_name(name: str) -> tuple[str, str]:
    parts = name.split()
    if len(parts) < 2:
        # Одно слово считаем именем: фамилию из него не угадать.
        return "", name
    return parts[-1], " ".join(parts[:-1])


# Собирает текст vCard 3.0 из заполненных полей контакта.
# Пустые поля не выводятся; строки разделяются CRLF, как требует стандарт.
def build_vcard(name: str, phone: str, email: str) -> str:
    lines = ["BEGIN:VCARD", "VERSION:3.0"]
    if name:
        # N: «фамилия;имя;отчество;...» - по этим частям телефонная книга
        # сортирует контакты, поэтому имя раскладываем на фамилию и имя.
        surname, given = split_name(name)
        lines.append(f"N:{vcard_escape(surname)};{vcard_escape(given)};;;")

    # FN в vCard 3.0 обязателен: без имени показываем телефон, затем почту.
    # Здесь оставляем полное имя как его ввели.
    lines.append(f"FN:{vcard_escape(name or phone or email)}")

    if phone:
        lines.append(f"TEL;TYPE=CELL:{vcard_escape(phone)}")
    if email:
        lines.append(f"EMAIL:{vcard_escape(email)}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


# Спрашивает три поля контакта. Хотя бы одно должно быть заполнено,
# иначе возвращает None и файл не создаётся.
def ask_contact() -> str | None:
    name = input("Имя: ").strip()
    phone = input("Телефон: ").strip()
    email = input("Почта: ").strip()

    # Строка из одних пробелов после strip() тоже считается пустой.
    if not (name or phone or email):
        print("Все поля контакта пусты. QR-код не создан.")
        return None

    # То же ограничение длины, что и у обычного текста, для каждого поля.
    for label, value in (("Имя", name), ("Телефон", phone), ("Почта", email)):
        if len(value) > MAX_TEXT_LENGTH:
            print(
                f"Поле «{label}»: {len(value)} символов, а максимум - {MAX_TEXT_LENGTH}. "
                "QR-код не создан."
            )
            return None

    return build_vcard(name, phone, email)


# Спрашивает ссылку или текст и проверяет его перед кодированием.
# Возвращает None, если ввод пустой или слишком длинный.
def ask_text() -> str | None:
    text = input("Введите ссылку или текст: ").strip()
    if not text:
        print("Ввод пуст. QR-код не создан.")
        return None

    # Ограничение в 1000 символов не даёт получить нечитаемый плотный QR.
    if len(text) > MAX_TEXT_LENGTH:
        print(
            f"Введено символов: {len(text)}, а максимум - {MAX_TEXT_LENGTH}. "
            "QR-код не создан."
        )
        return None

    return text


# Спрашивает цвет модулей и цвет фона.
# Непонятный или пустой ввод заменяется на чёрный и белый с сообщением.
def ask_colors() -> tuple[str, str]:
    raw_fill = input(f"Цвет модулей ({COLOR_HINT}; Enter - чёрный): ")
    raw_back = input(f"Цвет фона ({COLOR_HINT}; Enter - белый): ")

    fill = parse_color(raw_fill)
    back = parse_color(raw_back)

    # None означает, что цвет не распознан (пустая строка тоже даёт None).
    if fill is None:
        print("Цвет модулей не распознан - использую чёрный.")
        fill = "#000000"
    if back is None:
        print("Цвет фона не распознан - использую белый.")
        back = "#FFFFFF"

    return fill, back


# Спрашивает форму модулей.
# Пустой ввод молча даёт квадрат, непонятный - квадрат с сообщением.
def ask_shape() -> str:
    raw = input("Форма кода (квадрат, круг, сердце; Enter - квадрат): ").strip().lower()
    if not raw:
        return "square"
    if raw in SHAPES:
        return SHAPES[raw]

    print("Форма не распознана - использую квадрат.")
    return "square"


# Строит контур сердечка параметрической кривой
# и растягивает его на квадрат box×box.
def heart_polygon(box: float) -> list[tuple[float, float]]:
    raw = []
    for step in range(HEART_STEPS):
        t = 2 * math.pi * step / HEART_STEPS
        # Классическая формула: x = 16sin³t,
        # y = 13cos t − 5cos2t − 2cos3t − cos4t.
        raw.append(
            (
                16 * math.sin(t) ** 3,
                13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t),
            )
        )

    # Находим границы кривой, чтобы вписать её в клетку без обрезки.
    xs = [point[0] for point in raw]
    ys = [point[1] for point in raw]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    # Ось Y на экране направлена вниз, поэтому переворачиваем: (max(ys) - y).
    return [((x - min(xs)) / width * box, (max(ys) - y) / height * box) for x, y in raw]


# Рисует QR кружками или сердечками.
# Три угловые метки (глазки) всегда остаются квадратными,
# иначе сканер не сможет найти и выровнять код.
def render_shaped(
    size: int, matrix: list[list[bool]], border: int, fill: str, back: str, shape: str
) -> Image.Image:
    total = len(matrix)
    modules = total - 2 * border
    # Координаты (строка, столбец) левых верхних углов трёх глазков.
    # Каждый глазок занимает 7×7 модулей.
    eyes = (
        (border, border),
        (border, border + modules - 7),
        (border + modules - 7, border),
    )

    # Рисуем в увеличенном масштабе (до 4×), чтобы фигуры были гладкими,
    # а затем уменьшаем холст до точного размера size×size.
    scale = max(1, min(4, MAX_RENDER_SIDE // size))
    side = size * scale
    image = Image.new("RGB", (side, side), back)
    draw = ImageDraw.Draw(image)

    # Размер клетки под один модуль и отступ,
    # который центрирует сетку на холсте.
    box = side // total
    offset = (side - box * total) // 2
    # Контур сердечка считаем один раз и потом сдвигаем к каждому модулю.
    heart = heart_polygon(box) if shape == "heart" else []

    # Обходим матрицу: True - тёмный модуль, False - фон.
    for row in range(total):
        for col in range(total):
            if not matrix[row][col]:
                continue

            x = offset + col * box
            y = offset + row * box
            # Модули внутри глазков рисуем квадратами в любом режиме формы.
            if any(r0 <= row < r0 + 7 and c0 <= col < c0 + 7 for r0, c0 in eyes):
                draw.rectangle([x, y, x + box - 1, y + box - 1], fill=fill)
            elif shape == "circle":
                # Круг вписан в клетку модуля: диаметр равен её стороне.
                draw.ellipse([x, y, x + box - 1, y + box - 1], fill=fill)
            else:
                # Точки контура сердечка сдвигаем на позицию текущего модуля.
                draw.polygon([(x + px, y + py) for px, py in heart], fill=fill)

    # LANCZOS сглаживает края фигур при уменьшении холста.
    return image.resize((size, size), Image.LANCZOS)


# Спрашивает, что кодируем: 1 - ссылка/текст, 2 - контакт.
# Пустой ввод (Enter) - режим ссылки/текста, неверный ответ - повтор вопроса.
def ask_mode() -> str:
    while True:
        choice = input("Что кодируем? 1 - ссылка или текст, 2 - контакт (Enter - 1): ").strip()
        if not choice:
            return "1"
        if choice in ("1", "2"):
            return choice

        print("Неверный выбор: введите 1 или 2.")


# Точка входа: собирает данные, рисует QR-код и сохраняет qr.png.
def main() -> int:
    # 1. Что кодируем: контакт или обычный текст/ссылку.
    mode = ask_mode()

    if mode == "2":
        payload = ask_contact()
    else:
        payload = ask_text()

    # None означает, что проверки не прошли: файл создавать нельзя.
    if payload is None:
        return 1

    # 2. Параметры картинки.
    size = ask_size()
    fill, back = ask_colors()
    shape = ask_shape()

    # 3. Проверка контраста: код всё равно сохраним, но предупредим.
    ratio = contrast_ratio(fill, back)
    if ratio < MIN_CONTRAST:
        print(
            "Внимание: цвета модулей и фона слишком близки по яркости - "
            "сканер может не прочитать код."
        )
        print("Совет: используйте чёрный код на белом фоне.")
    # Контраста хватает, но код инвертирован: светлые модули на тёмном фоне.
    # Такие коды читают не все сканеры, поэтому предупреждаем отдельно.
    elif relative_luminance(hex_to_rgb(fill)) > relative_luminance(hex_to_rgb(back)):
        print(
            "Внимание: модули светлее фона - часть сканеров такой код не прочитает."
        )
        print("Совет: используйте тёмные модули на светлом фоне.")

    # Файл кладём рядом со скриптом, а не в текущую папку запуска.
    file_path = Path(__file__).resolve().parent / "qr.png"

    # 4. Кодируем данные. На слишком больших объёмах qrcode выбрасывает
    # DataOverflowError или ValueError - тогда файл не создаём.
    qr = qrcode.QRCode(border=4)
    qr.add_data(payload)
    try:
        qr.make(fit=True)
    except (qrcode.exceptions.DataOverflowError, ValueError):
        print("Текст слишком велик для QR-кода. Сократите его, файл не создан.")
        return 1

    # 5. Сколько пикселей достанется одному модулю (с учётом белой рамки).
    total_modules = qr.modules_count + 2 * qr.border
    box_size = max(1, size // total_modules)

    # Меньше 3 px на модуль - рисовать бессмысленно: код не считается,
    # а фигурные формы вообще ломаются. Сообщаем минимум для этих данных.
    if size < total_modules * MIN_PIXELS_PER_MODULE:
        print(
            "Слишком маленький размер: для этих данных нужно минимум "
            f"{total_modules * MIN_PIXELS_PER_MODULE} пикселей "
            f"(модулей: {total_modules}, по {MIN_PIXELS_PER_MODULE} px). QR-код не создан."
        )
        return 1

    # 6. Рисуем код. Квадрат - штатным рендером qrcode,
    # круг и сердце - вручную по матрице модулей.
    qr.box_size = box_size
    if shape == "square":
        image = qr.make_image(fill_color=fill, back_color=back).get_image()
        canvas = Image.new("RGB", (size, size), back)
        # Готовая картинка может быть меньше холста из-за округления -
        # ставим её по центру, добивая фон.
        canvas.paste(image.convert("RGB"), ((size - image.width) // 2, (size - image.height) // 2))
    else:
        canvas = render_shaped(size, qr.get_matrix(), qr.border, fill, back, shape)

    # 7. Сохранение. OSError бывает, если qr.png занят другой программой.
    try:
        canvas.save(file_path)
    except OSError as error:
        print(f"Не удалось сохранить файл {file_path}: {error}")
        return 1

    print(f"QR-код сохранён: {file_path} ({size}×{size} пикселей)")
    return 0


# Запуск программы с аккуратной обработкой Ctrl+C и закрытого ввода.
if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nВвод прерван. QR-код не создан.")
        sys.exit(1)
