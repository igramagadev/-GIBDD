import os
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
def generate_roster_image(title: str, headers: list[str], data: list[dict], output_path: str = None) -> BytesIO:
    """
    Generates a beautiful tabular image of the roster.
    data is a list of dictionaries. Each dictionary can either be:
      {"is_divider": True, "title": "Department Title", "color": (255, 100, 100)}
    OR
      {"row": ["col1", "col2", "col3", "col4", "col5", "col6"], "bg_color": (240, 240, 240)}
    """
    try:
        font_main = ImageFont.truetype("arial.ttf", 16)
        font_header = ImageFont.truetype("arialbd.ttf", 18)
        font_title = ImageFont.truetype("arialbd.ttf", 22)
    except Exception:
        font_main = ImageFont.load_default()
        font_header = ImageFont.load_default()
        font_title = ImageFont.load_default()
    col_widths = [180, 250, 100, 200, 280, 120]
    total_width = sum(col_widths) + len(col_widths) * 2
    row_height = 30
    total_rows = 1 
    total_rows += 1 
    for item in data:
        total_rows += 1
    img_height = total_rows * row_height + 40 
    img_width = total_width + 40
    img = Image.new("RGB", (img_width, img_height), "white")
    draw = ImageDraw.Draw(img)
    y = 20
    x_start = 20
    try:
        bbox = draw.textbbox((0, 0), title, font=font_title)
        tw = bbox[2] - bbox[0]
    except Exception:
        tw = 200
    draw.text((img_width//2 - tw//2, y), title, fill="black", font=font_title)
    y += row_height + 10
    x = x_start
    draw.rectangle([x, y, x + total_width, y + row_height], fill=(173, 216, 230), outline="black")
    for i, h in enumerate(headers):
        draw.rectangle([x, y, x + col_widths[i], y + row_height], outline="black")
        try:
            bbox = draw.textbbox((0, 0), h, font=font_header)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except Exception:
            tw, th = 50, 10
        draw.text((x + col_widths[i]//2 - tw//2, y + row_height//2 - th//2 - 2), h, fill="black", font=font_header)
        x += col_widths[i]
    y += row_height
    for item in data:
        x = x_start
        if item.get("is_divider"):
            bg_color = item.get("color", (255, 150, 150))
            draw.rectangle([x, y, x + total_width, y + row_height], fill=bg_color, outline="black")
            text = item.get("title", "")
            try:
                bbox = draw.textbbox((0, 0), text, font=font_header)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
            except Exception:
                tw, th = 50, 10
            draw.text((img_width//2 - tw//2, y + row_height//2 - th//2 - 2), text, fill="black", font=font_header)
        else:
            bg_color = item.get("bg_color", (255, 255, 255))
            row_data = item.get("row", [""] * len(col_widths))
            for i, cell in enumerate(row_data):
                draw.rectangle([x, y, x + col_widths[i], y + row_height], fill=bg_color, outline="black")
                text = str(cell)
                if len(text) > 30: text = text[:27] + "..."
                try:
                    bbox = draw.textbbox((0, 0), text, font=font_main)
                    tw = bbox[2] - bbox[0]
                    th = bbox[3] - bbox[1]
                except Exception:
                    tw, th = 50, 10
                draw.text((x + col_widths[i]//2 - tw//2, y + row_height//2 - th//2 - 2), text, fill="black", font=font_main)
                x += col_widths[i]
        y += row_height
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    if output_path:
        img.save(output_path)
    return buffer
async def update_cpps_roster(guild):
    import disnake
    from config.constants import settings
    from database import get_user
    channel = guild.get_channel(settings.roster_channel_id)
    if not channel:
        return
    cpps_role_id = settings.department_role_ids.get("ЦППС", 0)
    if not cpps_role_id:
        return
    cpps_role = guild.get_role(cpps_role_id)
    if not cpps_role:
        return
    categories = {
        "Руководство ЦППС": {"color": (255, 100, 100), "members": []},
        "Начальство Курсов ЦППС": {"color": (255, 150, 150), "members": []},
        "Преподавательский состав ЦППС": {"color": (100, 150, 255), "members": []},
        "Стажёры ЦППС": {"color": (150, 200, 255), "members": []},
    }
    for member in cpps_role.members:
        db_user = get_user(member.id)
        if not db_user:
            continue
        static_id = db_user.get("static_id", "Не указан")
        rank = db_user.get("rank", "")
        position = "Неизвестно"
        for role in member.roles:
            if role.id in settings.position_role_ids.values():
                position = role.name
                break
        pos_lower = position.lower()
        if "начальник цппс" in pos_lower or "зам" in pos_lower and "цппс" in pos_lower:
            cat = "Руководство ЦППС"
        elif "нач.курсов" in pos_lower or "инструктор" in pos_lower:
            cat = "Начальство Курсов ЦППС"
        elif "преподаватель" in pos_lower:
            cat = "Преподавательский состав ЦППС"
        else:
            cat = "Стажёры ЦППС"
        joined = getattr(member, "joined_at", None)
        joined_str = joined.strftime("%d.%m.%Y") if joined else "Неизвестно"
        name = db_user.get("nickname", member.display_name)
        categories[cat]["members"].append({
            "id": str(member.id),
            "name": name,
            "static": static_id,
            "rank": rank,
            "position": position,
            "joined": joined_str
        })
    data = []
    for cat_name, cat_data in categories.items():
        data.append({"is_divider": True, "title": cat_name, "color": cat_data["color"]})
        for idx, m in enumerate(cat_data["members"]):
            bg = (240, 240, 240) if idx % 2 == 0 else (255, 255, 255)
            data.append({"row": [m["id"], m["name"], m["static"], m["rank"], m["position"], m["joined"]], "bg_color": bg})
    headers = ["Discord ID", "Персональные данные", "Номер УДО", "Специальное звание", "Занимаемая должность", "Дата вступления"]
    buffer = generate_roster_image("Состав ЦППС", headers, data)
    file = disnake.File(fp=buffer, filename="roster.png")
    try:
        async for msg in channel.history(limit=20):
            if msg.author == guild.me and len(msg.attachments) > 0:
                await msg.edit(content="Обновленный состав ЦППС", file=file)
                return
        await channel.send(content="Обновленный состав ЦППС", file=file)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to update roster: {e}")