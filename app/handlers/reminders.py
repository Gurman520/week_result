import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler, CallbackQueryHandler, MessageHandler,
    filters, ConversationHandler, ContextTypes
)
from handlers.common import cancel
from database import get_user, upsert_user
from scheduler import schedule_reminder


logger = logging.getLogger(__name__)

FREQ, CHOOSE_DAYS, DAY_WEEK, DAY_MONTH, TIME_INPUT = range(5)
DAY_NAMES = {
    0: "Вс", 1: "Пн", 2: "Вт", 3: "Ср", 4: "Чт", 5: "Пт", 6: "Сб"
}

async def set_reminder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сбрасывает любые предыдущие диалоги и запускает настройку."""
    user_id = update.effective_user.id
    logger.info(f"User {user_id} triggered /set_reminder")
    # Очищаем все данные предыдущих диалогов
    context.user_data.clear()
    keyboard = [
        [InlineKeyboardButton("Каждый день", callback_data="day"),
         InlineKeyboardButton("Раз в неделю", callback_data="week"),
         InlineKeyboardButton("Раз в месяц", callback_data="month")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выбери частоту напоминаний:", reply_markup=reply_markup)
    return FREQ

async def freq_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    freq = query.data
    context.user_data['freq'] = freq
    logger.info(f"Frequency chosen: {freq}")

    if freq == 'day':
        keyboard = [
            [InlineKeyboardButton("Пн", callback_data="day_1"),
             InlineKeyboardButton("Вт", callback_data="day_2"),
             InlineKeyboardButton("Ср", callback_data="day_3")],
            [InlineKeyboardButton("Чт", callback_data="day_4"),
             InlineKeyboardButton("Пт", callback_data="day_5"),
             InlineKeyboardButton("Сб", callback_data="day_6")],
            [InlineKeyboardButton("Вс", callback_data="day_0")],
            [InlineKeyboardButton("✅ Все дни", callback_data="days_all"),
             InlineKeyboardButton("Готово", callback_data="days_done")]
        ]
        await query.edit_message_text("Выбери дни недели (можно несколько):", reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['selected_days'] = set()
        return CHOOSE_DAYS
    elif freq == 'week':
        keyboard = [
            [InlineKeyboardButton("Пн", callback_data="1"), InlineKeyboardButton("Вт", callback_data="2"),
             InlineKeyboardButton("Ср", callback_data="3"), InlineKeyboardButton("Чт", callback_data="4"),
             InlineKeyboardButton("Пт", callback_data="5")],
            [InlineKeyboardButton("Сб", callback_data="6"), InlineKeyboardButton("Вс", callback_data="0")]
        ]
        await query.edit_message_text("Выбери день недели:", reply_markup=InlineKeyboardMarkup(keyboard))
        return DAY_WEEK
    elif freq == 'month':
        await query.edit_message_text("Введи число месяца (1-31). Если дней меньше, напоминание будет в последний день.")
        return DAY_MONTH

async def choose_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    selected = context.user_data.get('selected_days', set())

    if data == "days_all":
        selected = {0, 1, 2, 3, 4, 5, 6}
        context.user_data['selected_days'] = selected
        await query.answer("Выбраны все дни недели")
    elif data == "days_done":
        if not selected:
            await query.edit_message_text("Не выбрано ни одного дня. Выбери хотя бы один.")
            return CHOOSE_DAYS
        context.user_data['custom_days'] = ','.join(map(str, sorted(selected)))
        logger.info(f"Selected days: {context.user_data['custom_days']}")
        await query.edit_message_text("Введи время в формате ЧЧ:ММ (например, 18:00):")
        return TIME_INPUT
    else:  # day_0..day_6
        day = int(data.split('_')[1])
        if day in selected:
            selected.remove(day)
        else:
            selected.add(day)
        context.user_data['selected_days'] = selected
        # Можно обновлять клавиатуру с отметками, но для простоты оставим так
        await query.answer(f"Выбрано дней: {len(selected)}")

    # Обновляем клавиатуру (показываем текущий выбор – можно улучшить, но не обязательно)
    return CHOOSE_DAYS

async def day_week_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['day_week'] = int(query.data)
    await query.edit_message_text("Введи время в формате ЧЧ:ММ (например, 18:00):")
    return TIME_INPUT

async def day_month_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        day = int(update.message.text)
        if 1 <= day <= 31:
            context.user_data['day_month'] = day
            await update.message.reply_text(
                "Введи время в формате ЧЧ:ММ (например, 18:00).\n"
                "Если в каком-то месяце меньше дней, напоминание придёт в последний день месяца."
            )
            return TIME_INPUT
        else:
            await update.message.reply_text("Число должно быть от 1 до 31. Попробуй ещё раз:")
            return DAY_MONTH
    except ValueError:
        await update.message.reply_text("Введи число. Например: 15")
        return DAY_MONTH

async def time_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    try:
        hour, minute = map(int, text.split(':'))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except:
        await update.message.reply_text("Неверный формат. Введи время как ЧЧ:ММ, например 09:30:")
        return TIME_INPUT

    user_id = update.effective_user.id
    freq = context.user_data['freq']
    day_of_week = context.user_data.get('day_week', 4)
    day_of_month = context.user_data.get('day_month', 1)
    custom_days = context.user_data.get('custom_days', '')

    await upsert_user(user_id, frequency=freq, day_of_week=day_of_week,
                      day_of_month=day_of_month, time_hour=hour, time_minute=minute,
                      custom_days=custom_days)
    user_new = await get_user(user_id)
    schedule_reminder(context.application, user_new)

    # Формируем информативное описание расписания
    time_str = f"{hour:02d}:{minute:02d}"
    if freq == 'day':
        if custom_days:
            selected = list(map(int, custom_days.split(',')))
            days_str = ', '.join(DAY_NAMES[d] for d in selected)
            description = f"по выбранным дням ({days_str})"
        else:
            description = "каждый день"
    elif freq == 'week':
        day_name = DAY_NAMES[day_of_week]
        description = f"каждую неделю по {day_name}"
    elif freq == 'month':
        # Учитываем возможный перенос на последний день месяца
        if day_of_month > 28:
            note = " (если в месяце меньше дней — в последний день)"
        else:
            note = ""
        description = f"каждый месяц {day_of_month}-го числа{note}"

    await update.message.reply_text(
        f"Настройки сохранены! Буду напоминать {description} в {time_str}."
    )
    logger.info(f"Reminder set for user {user_id}: {freq} at {time_str}")
    return ConversationHandler.END

reminder_conv = ConversationHandler(
    entry_points=[CommandHandler('set_reminder', set_reminder_start)],
    states={
        FREQ: [CallbackQueryHandler(freq_chosen)],
        CHOOSE_DAYS: [CallbackQueryHandler(choose_days)],
        DAY_WEEK: [CallbackQueryHandler(day_week_chosen)],
        DAY_MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, day_month_input)],
        TIME_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, time_input)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
    per_message=False
)
