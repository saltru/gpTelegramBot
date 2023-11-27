from __future__ import print_function

import datetime
from datetime import *

import os.path
import logging
import configparser

from typing import Final

from telegram import *
from telegram.ext import *

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

today = date.today()

logging.getLogger("telegram").disabled = True
logging.getLogger("apscheduler").disabled = True
logging.getLogger("httpx").disabled = True
logging.getLogger("googleapiclient").disabled = True
logging.getLogger("googleapiclient.discovery_cache").disabled = True
logging.getLogger("telegram.ext").disabled = True
logging.getLogger("google_auth_httplib2").disabled = True

logger = logging.getLogger("gp-bot-logger")

#button callback actions
BOTACTION_SET_CURRENT_EXPERIMENT: Final = "0"
BOTACTION_START_EXPERIMENT_TODAY: Final = "1"
BOTACTION_SHOW_SPECIFIC_E2_TEST: Final  = "2"
BOTACTION_REPORT_E2_TEST_DONE: Final    = "3"
BOTACTION_REPORT_E2_TEST_SKIP: Final    = "4"
BOTACTION_REPORT_E2_TEST_RESULTS: Final = "5"

#message callback actions
MSGACTION_DEFAULT: Final = "6"
MSGACTION_REPORT_E2_TEST_RESULTS: Final = "7"
MSGACTION_REPORT_E2_TEST_RESULTS_UNITS: Final = "8"

#Global credentials and google API service
creds = None
gAPI_service = None

#Global variables
bot_token: str = ""
admin_chatid: str = ""
spreadsheet_id: str = ""


def spreadsheet_get_service():
    scopes = ["https://www.googleapis.com/auth/drive",
              "https://www.googleapis.com/auth/drive.file",
              "https://www.googleapis.com/auth/spreadsheets"]

    global creds
    global gAPI_service

    if not gAPI_service:
        secret_file = os.path.join(os.getcwd(), 'girlpower-e2-7f58ce190de1.json')
        creds = service_account.Credentials.from_service_account_file(filename=secret_file, scopes=scopes)

        try:
            gAPI_service = build('sheets', 'v4', credentials=creds)
        except HttpError as error:
            logger.error(f"gAPI_service build error: {error}")
            return error

    return gAPI_service


def spreadsheet_get_values(spreadsheet_id, range_name):
    service = spreadsheet_get_service()

    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=range_name).execute()
        rows = result.get('values', [])
        return rows
    except HttpError as error:
        logger.error(f"spreadsheet_get_values: {error}")
        return error


def spreadsheet_update_values(spreadsheet_id, range_name, values):
    service = spreadsheet_get_service()

    try:
        body = {
            'values': values,
            'majorDimension': 'COLUMNS',
        }

        result = service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=range_name,
            valueInputOption='USER_ENTERED', body=body).execute()

        return
    except HttpError as error:
        logger.error(f"spreadsheet_update_values: {error}")
        return


def spreadsheet_get_sheets():
    service = spreadsheet_get_service()

    try:
        result = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id).execute()

        sheet_titles = []
        for sheet in result.get('sheets', []):
            sheet_titles.append(sheet.get("properties", {}).get('title'))

        return sheet_titles
    except HttpError as error:
        logger.error(f"spreadsheet_get_sheets: {error}")
        return error


def spreadsheet_get_usernames(range_name):
    rows = spreadsheet_get_values(spreadsheet_id, range_name)

    result = []
    for username in rows:
        if username:
            result.append(username[0])
        else:
            result.append('')
    return result


def spreadsheet_get_username_row_index(username: str, experiment: str) -> int:
    rows = spreadsheet_get_values(spreadsheet_id, f"{experiment}!A3:A")

    for index, next_user in enumerate(rows):
        if username in next_user:
            return int(index)

    return -1

"""
Bot logic
"""


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username: str | None = update.message.from_user.username
    chat_id: int = update.message.chat_id
    logger.info(f"START User: {username} chat_id={chat_id}")

    experiments = spreadsheet_get_sheets()

    active_experiments = []
    for experiment in experiments:
        if username in spreadsheet_get_usernames(f"{experiment}!A3:A"):
            active_experiments.append([InlineKeyboardButton(
                experiment,
                callback_data=';'.join([experiment, BOTACTION_SET_CURRENT_EXPERIMENT, ""])
            )])

    #User is not presented in any sheet (experiment)
    if not active_experiments:
        logger.warning(f"Username {username} NOT in the list. Rejected.")
        return

    context.user_data["username"] = username
    context.user_data["chat_id"] = chat_id
    context.user_data["action"] = MSGACTION_DEFAULT

    reply_markup = InlineKeyboardMarkup(active_experiments)

    await update.message.reply_text(f"Здравствуйте, {update.message.from_user.first_name}!\n\n"
                                    f"С помощью этого бота, вы можете сообщать результаты анализов на эстрадиол. \n\n"
                                    f"Выберите нужное действие, нажав на кнопку в сообщении.\n" + \
                                    f"Или введите команду /start, чтобы перейти к выбору текущей фазы эксперимента (в начало).\n\n"
                                    f"Вы можете пообщаться с администратором эксперимента, просто написав ваш вопрос "
                                    f"боту, как в обычный чат. Администратор ответит так же через бота. "
                                    f"Поддерживается общение только текстом, без голосовых и фото.\n\n"
                                    f"Выберите текущую фазу эксперимента (сделанный укол):",
                                    reply_markup=reply_markup)


async def admin_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id: str = context.args[0]
    msg_id: str = context.args[1]
    admin_answer: str = "\n".join(update.message.text.splitlines()[1:])

    logger.info(f"Admin sent direct reply to chat_id={chat_id}")

    await context.bot.send_message(chat_id = chat_id,
                            reply_to_message_id=msg_id,
                            text=admin_answer)
    await context.bot.send_message(chat_id = admin_chatid,
                            text=f"SEND TO #{chat_id}\n"
                                f"MSG_ID: {msg_id}\n"
                                f"==============================\n"
                                f"{admin_answer}")


async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    user_input: str = update.message.text

    bot_action: str = context.user_data.get("action")
    bot_answer: str = f"DEFAULT"
    reply_markup: ReplyKeyboardMarkup = ReplyKeyboardMarkup([])
    ReplyKeyboardRemove()

    if not bot_action or bot_action == MSGACTION_DEFAULT:
        #Admin chat
        username: str = update.message.from_user.username
        firstname: str = update.message.from_user.first_name
        chat_id: int = update.message.chat_id
        msg_id: int = update.message.message_id

        logger.info(f"User {username} sent a direct message to admin")

        await context.bot.send_message(chat_id=admin_chatid,
                                 text=f"RECEIVED FROM #{chat_id}\n"
                                      f"USER: {username}({firstname})\n"
                                     f"CHAT ID: {chat_id}\n"
                                     f"MSG_ID: {msg_id}\n"
                                      f"/answer {chat_id} {msg_id}"
                                     f"==============================\n"
                                     f"{user_input}")
        return str()
    elif bot_action == MSGACTION_REPORT_E2_TEST_RESULTS:
        if user_input.isdecimal():
            context.user_data["e2_value"] = user_input
            context.user_data["action"] = MSGACTION_REPORT_E2_TEST_RESULTS_UNITS

            reply_markup = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton("пг/мл"), KeyboardButton("пмоль/л")]
                ],
                resize_keyboard=True,
                one_time_keyboard=True
            )

            bot_answer = f"Принято! Теперь укажите единицы измерения этого результата."
        else:
            bot_answer = (f"Введите число: результат вашего анализа на эстрадиол (E2) "
                          f"без единицы измерения (ее можно будет выбрать на следующем шаге)")
    elif bot_action == MSGACTION_REPORT_E2_TEST_RESULTS_UNITS:
        username: str = context.user_data.get("username")
        chat_id: int = context.user_data.get("chat_id")
        experiment: str = context.user_data.get("experiment")
        days_after_shot: str = context.user_data.get("days_after_shot")
        e2_value: str = context.user_data.get("e2_value")

        index: int = spreadsheet_get_username_row_index(username, experiment) + 3  # data starts from the 3rd row

        if user_input == "пг/мл":
            e2_value_converted: str = str(int(e2_value))
            test_cell_range = get_e2_test_range(username, experiment, days_after_shot)
            spreadsheet_update_values(spreadsheet_id, test_cell_range, [[e2_value_converted]])

            logger.info(f"TEST REPORT User {username} {e2_value_converted} pg/ml "
                        f"{experiment} Day: {days_after_shot}")

            context.user_data["action"] = MSGACTION_DEFAULT
            bot_answer = f"Спасибо! Результат {e2_value} пг/мл записан успешно."
        elif user_input == "пмоль/л":
            e2_value_converted: str = str(int(float(e2_value) / 3.67))
            test_cell_range = get_e2_test_range(username, experiment, days_after_shot)
            spreadsheet_update_values(spreadsheet_id, test_cell_range, [[e2_value_converted]])

            logger.info(f"TEST REPORT User {username} {e2_value_converted} pg/ml "
                        f"{experiment} Day: {days_after_shot}")

            context.user_data["action"] = MSGACTION_DEFAULT
            bot_answer = f"Спасибо! Результат {e2_value_converted} пг/мл ({e2_value} пмоль/л) записан успешно."
        else:
            reply_markup = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton("пг/мл"), KeyboardButton("пмоль/л")]
                ],
                resize_keyboard=True,
                one_time_keyboard=True
            )

            bot_answer = f"Выберите единицы измерения результата из списка."

    await update.message.reply_text(text=bot_answer,
                                    reply_markup=reply_markup)


async def error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f'FAILURE: Update {update} caused error {context.error}')


async def set_current_experiment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    username: str = update.callback_query.from_user.username
    chat_id: int = update.callback_query.message.chat_id
    experiment, action, days_after_shot = update.callback_query.data.split(sep=";")

    index: int = spreadsheet_get_username_row_index(username, experiment) + 3

    start_date = spreadsheet_get_values(spreadsheet_id, f"{experiment}!B{index}")

    if not start_date:
        buttons = [[InlineKeyboardButton(
            f"Начать фазу (укол сделан {date.today()})",
            callback_data=';'.join([experiment, BOTACTION_START_EXPERIMENT_TODAY, ""])
        )]]

        reply_markup = InlineKeyboardMarkup(buttons)

        await context.bot.send_message(chat_id=chat_id,
                                       text=f"Фаза: {experiment}\n"
                                            f"Статус: Не начата\n\n"
                                            f"Чтобы начать эту фазу, сделайте укол препаратом и нажмите \"Начать\". "
                                            f"Бот запомнит сегодняшнюю дату и поставит напоминалки для сдачи анализов.\n",
                                       reply_markup=reply_markup)
    else:
        await show_current_experiment(update, context)


async def start_experiment_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    username: str = update.callback_query.from_user.username
    chat_id: int = update.callback_query.message.chat_id
    experiment, action, days_after_shot = update.callback_query.data.split(sep=";")

    index: int = spreadsheet_get_username_row_index(username, experiment) + 3

    spreadsheet_update_values(spreadsheet_id,
                                  f"{experiment}!B{index}:C{index}",
                                  [[str(date.today())], [str(chat_id)]])

    logger.info(f"EXP STARTED User {username} Experiment {experiment} ")


async def show_current_experiment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    username: str = update.callback_query.from_user.username
    chat_id: int = update.callback_query.message.chat_id
    experiment, action, days_after_shot = update.callback_query.data.split(sep=";")

    index: int = spreadsheet_get_username_row_index(username, experiment) + 3

    dt = spreadsheet_get_values(spreadsheet_id, f"{experiment}!B{index}")[0]
    start_date = datetime.strptime(dt[0], "%Y-%m-%d")

    buttons = []
    test_intervals = spreadsheet_get_values(spreadsheet_id, f"{experiment}!D2:2")[0]

    for test_interval in test_intervals:
        test_date = start_date + timedelta(days=int(test_interval))
        buttons.append([InlineKeyboardButton(
            f"День {test_interval} - {test_date.strftime('%d-%b-%Y')}",
            callback_data=';'.join([experiment, BOTACTION_SHOW_SPECIFIC_E2_TEST, test_interval])
        )])

    reply_markup = InlineKeyboardMarkup(buttons)

    logger.info(f"EXP REQ User {username} Experiment {experiment}")

    await context.bot.send_message(chat_id=chat_id,
                                       text=f"Фаза: {experiment}\n"
                                            f"Статус: Начата\n"
                                            f"Дата: {start_date.strftime('%d-%b-%Y')}\n\n"
                                            f"Нажмите на кнопку анализа, чтобы отправить результаты. "
                                            f"Если вы отправили неверные данные, "
                                            f"просто отправьте правильные данные еще раз.\n",
                                       reply_markup=reply_markup)


def get_e2_test_range(username: str, experiment: str, days_after_shot: str) -> str:
    index: int = spreadsheet_get_username_row_index(username, experiment) + 3  # data starts from the 3rd row

    test_intervals_raw = spreadsheet_get_values(spreadsheet_id, f"{experiment}!D2:2")[0]

    test_index: int = test_intervals_raw.index(days_after_shot)
    # data starts from D column
    test_index_char: str = chr(ord('D') + test_index)

    range_name: str = f"{experiment}!{test_index_char}{index}"

    return range_name


async def show_specific_e2_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    username: str = update.callback_query.from_user.username
    chat_id: int = update.callback_query.message.chat_id
    experiment, action, days_after_shot = update.callback_query.data.split(sep=";")

    test_cell_range: str = get_e2_test_range(username, experiment, days_after_shot)

    test_value_raw: list = spreadsheet_get_values(spreadsheet_id, test_cell_range)
    test_value: str = test_value_raw[0][0] if test_value_raw else ""

    buttons = []
    reply_text: str = ""
    if test_value == "V":
        buttons = [[InlineKeyboardButton(
            f"Отправить результаты анализа",
            callback_data=';'.join([experiment, BOTACTION_REPORT_E2_TEST_RESULTS, days_after_shot])
        )]]
        reply_text = (f"*Анализ:* {days_after_shot} день ({experiment})\n"
                      f"*Статус:* Сдан\n"
                      f"*Результат:* Не готов\n\n"
                      f"Анализ сдан. Отправьте результаты, когда они будут готовы.\n")
    elif test_value == "X":
        buttons = [[InlineKeyboardButton(
            f"Отправить результаты анализа",
            callback_data=';'.join([experiment, BOTACTION_REPORT_E2_TEST_RESULTS, days_after_shot])
        )]]
        reply_text = (f"*Анализ:* {days_after_shot} день ({experiment})\n"
                      f"*Статус:* Отменен\n"
                      f"*Результат:* Не готов\n\n"
                      f"Анализ был отменен! Если это ошибка и анализ на {days_after_shot}-й день "
                      f"все-таки был сдан - нажмите \"Отправить результаты\".\n")
    elif test_value.isdigit():
        buttons = [[InlineKeyboardButton(
            f"Изменить результаты анализа",
            callback_data=';'.join([experiment, BOTACTION_REPORT_E2_TEST_RESULTS, days_after_shot])
        )]]
        reply_text = (f"*Анализ:* {days_after_shot} день ({experiment})\n"
                      f"*Статус:* Сдан\n"
                      f"*Результат:* {test_value} пг/мл\n\n"
                      f"Анализ сдан, результаты готовы.\n")
    else:
        buttons = [[InlineKeyboardButton(f"Анализ сдан",
                                        callback_data=';'.join(
                                            [experiment, BOTACTION_REPORT_E2_TEST_DONE, days_after_shot])),
                   InlineKeyboardButton(f"Пропустить анализ",
                                        callback_data=';'.join(
                                            [experiment, BOTACTION_REPORT_E2_TEST_SKIP, days_after_shot]))
                  ]]
        reply_text = (f"*Анализ:* {days_after_shot} день ({experiment})\n"
                      f"*Статус:* Не сдан\n"
                      f"*Результат:* Не готов\n\n"
                      f"Сдайте анализ на эстрадиол. Подтвердите сдачу нажатием кнопки \"Анализ сдан\".\n"
                      f"Если не получается сдать анализ, нажмите \"Пропустить анализ\".\n")

    logger.info(f"TEST REQ User {username} Experiment {experiment} Day {days_after_shot}")

    reply_markup = InlineKeyboardMarkup(buttons)
    await context.bot.send_message(chat_id=chat_id,
                                    text=reply_text,
                                    parse_mode="Markdown",
                                    reply_markup=reply_markup)


async def e2_test_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    username: str = update.callback_query.from_user.username
    chat_id: int = update.callback_query.message.chat_id
    experiment, action, days_after_shot = update.callback_query.data.split(sep=";")

    index: int = spreadsheet_get_username_row_index(username, experiment) + 3   #data starts from the 3rd row

    test_cell_range = get_e2_test_range(username, experiment, days_after_shot)
    spreadsheet_update_values(spreadsheet_id, test_cell_range, [['V']])

    logger.info(f"TEST DONE User {username} Experiment {experiment} Day {days_after_shot}")

    buttons = [[InlineKeyboardButton(
        f"Отправить результаты анализа",
        callback_data=';'.join([experiment, BOTACTION_REPORT_E2_TEST_RESULTS, days_after_shot])
    )]]

    reply_markup = InlineKeyboardMarkup(buttons)
    await update.callback_query.message.edit_text(
                                        text=(f"*Анализ:* {days_after_shot} день ({experiment})\n"
                                            f"*Статус:* Сдан\n"
                                            f"*Результат:* Не готов\n\n"
                                            f"Анализ сдан. Отправьте результаты, когда они будут готовы.\n"),
                                        parse_mode="Markdown",
                                        reply_markup=reply_markup)


async def e2_test_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    username: str = update.callback_query.from_user.username
    chat_id: int = update.callback_query.message.chat_id
    experiment, action, days_after_shot = update.callback_query.data.split(sep=";")

    index: int = spreadsheet_get_username_row_index(username, experiment) + 3   #data starts from the 3rd row

    test_cell_range = get_e2_test_range(username, experiment, days_after_shot)
    spreadsheet_update_values(spreadsheet_id, test_cell_range, [['X']])
    logger.warning(f"TEST SKIP User {username} Experiment {experiment} Day {days_after_shot}")

    buttons = [[InlineKeyboardButton(
        f"Отправить результаты анализа",
        callback_data=';'.join([experiment, BOTACTION_REPORT_E2_TEST_RESULTS, days_after_shot])
    )]]

    reply_markup = InlineKeyboardMarkup(buttons)
    await update.callback_query.message.edit_text(
                                        text=(f"*Анализ:* {days_after_shot} день ({experiment})\n"
                                            f"*Статус:* Отменен\n"
                                            f"*Результат:* Не готов\n\n"
                                            f"Анализ был отменен! Если это ошибка и анализ на {days_after_shot} день "
                                            f"все-таки был сдан - нажмите \"Отправить результаты\".\n"),
                                        parse_mode="Markdown",
                                        reply_markup=reply_markup)


async def e2_test_report_results(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    username: str = update.callback_query.from_user.username
    chat_id: int = update.callback_query.message.chat_id
    experiment, action, days_after_shot = update.callback_query.data.split(sep=";")

    index: int = spreadsheet_get_username_row_index(username, experiment) + 3   #data starts from the 3rd row

    context.user_data["username"] = username
    context.user_data["chat_id"] = chat_id
    context.user_data["experiment"] = experiment
    context.user_data["action"] = MSGACTION_REPORT_E2_TEST_RESULTS
    context.user_data["days_after_shot"] = days_after_shot

    await update.callback_query.message.delete()
    await context.bot.send_message(chat_id=chat_id,
                                    text=(f"*Анализ:* {days_after_shot} день ({experiment})\n"
                                          f"1. Сначала введите результат, как целое число, без единиц измерения. Например, 789.\n"
                                          f"2. Потом бот предложит выбрать единицы измерения (пг/мл или пмоль/л) из списка "
                                          f"и автоматически сконвертирует результат в пг/мл.\n\n"
                                            f"Введите значение E2 (эстрадиол):"),
                                    parse_mode="Markdown",
                                    reply_markup=ReplyKeyboardRemove())


async def button_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parses the CallbackQuery and updates the message text."""
    query = update.callback_query
    await query.answer()
    callback_data = query.data.split(sep=';')

    if callback_data[1] == BOTACTION_SET_CURRENT_EXPERIMENT:
        await set_current_experiment(update, context)
    elif callback_data[1] == BOTACTION_START_EXPERIMENT_TODAY:
        await start_experiment_today(update, context)
        await show_current_experiment(update, context)
    elif callback_data[1] == BOTACTION_SHOW_SPECIFIC_E2_TEST:
        await show_specific_e2_test(update, context)
    elif callback_data[1] == BOTACTION_REPORT_E2_TEST_DONE:
        await e2_test_done(update, context)
    elif callback_data[1] == BOTACTION_REPORT_E2_TEST_SKIP:
        await e2_test_skip(update, context)
    elif callback_data[1] == BOTACTION_REPORT_E2_TEST_RESULTS:
        await e2_test_report_results(update, context)


async def send_user_notifications(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the alarm messages."""
    job = context.job

    logger.info("Sending all notifications...")

    experiments = spreadsheet_get_sheets()

    now = datetime.now()
    logger.info(f"now()={now} now.time().hour={now.time().hour}")

    for experiment in experiments:

        usernames = spreadsheet_get_usernames(f"{experiment}!A3:A")
        chat_ids = spreadsheet_get_usernames(f"{experiment}!C3:C")
        test_intervals = spreadsheet_get_values(spreadsheet_id, f"{experiment}!D2:2")[0]

        for i in range(len(usernames)):
            username: str = usernames[i]
            if not username or username == '':
                #logger.warning(f"Skipped blank username, experiment {experiment}")
                continue

            #if chat_id not exist - skip this user
            try:
                chat_id = int(chat_ids[i])
            except:
                #logger.warning(f"Skipped {username} without chat_id, experiment {experiment}")
                continue

            #index = spreadsheet_get_username_row_index(username, experiment) + 3
            index = i + 3
            dt = spreadsheet_get_values(spreadsheet_id, f"{experiment}!B{index}")[0]
            start_date = datetime.strptime(dt[0], "%Y-%m-%d")

            for test_interval in test_intervals:
                alarm_prev_day = start_date + timedelta(days=int(test_interval)-1)
                alarm_cur_day = start_date + timedelta(days=int(test_interval))

                #now() - in UTC tzinfo=None
                if now.date() == alarm_prev_day.date() and now.time().hour >= 10:
                    #Prev-day user notification
                    logger.info(f"Notification about {experiment} day {test_interval} "
                                f"test has been sent to {username} chat_id={chat_id}")
                    await context.bot.send_message(chat_id,
                                                   text=f"Напоминаю, что завтра, {alarm_cur_day.date()}, у вас {test_interval}-й день "
                                                        f"после укола \"{experiment}\". А значит, надо будет сдать анализ на эстрадиол.\n\n"
                                                        f"После сдачи анализа, нужно будет отметить это в боте.\n"
                                                        f"Для этого, надо выбрать в меню кнопку \"{experiment}\", затем "
                                                        f"\"День {test_interval}\" и далее нажать \"Анализ сдан\".\n\n"
                                                        f"Если вы по какой-то причине не можете сдать анализ, нажмите \"Пропустить анализ\" "
                                                        f"в том же меню."
                                                        )
                elif now.date() == alarm_cur_day.date() and now.time().hour < 10:
                    #At the day user notification
                    logger.info(f"Alarm about {experiment} day {test_interval} "
                                f"test has been sent to {username} chat_id={chat_id}")
                    await context.bot.send_message(chat_id,
                                                   text=f"Cегодня, {alarm_cur_day.date()}, у вас {test_interval}-й день "
                                                        f"после укола \"{experiment}\". А значит, надо сдать анализ на эстрадиол!\n\n"
                                                        f"ВАЖНО! После сдачи анализа, не забудьте отметиться в боте.\n"
                                                        f"Для этого, надо выбрать в меню кнопку \"{experiment}\", затем "
                                                        f"\"День {test_interval}\" и далее нажать \"Анализ сдан\".\n\n"
                                                        f"Если вы по какой-то причине не можете сдать анализ, нажмите \"Пропустить анализ\" "
                                                        f"в том же меню."
                                                        )


if __name__ == '__main__':
    #disable all existing loggers from imported modules
    for lg in [logging.getLogger(name) for name in logging.root.manager.loggerDict]:
        lg.disabled = True
    #but enable GP bot module logger
    logger.disabled = False

    #initialize logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s:%(name)s:%(message)s",
                        datefmt="%d-%b-%y %H:%M:%S")

    logger.info("Loading config.ini...")
    config = configparser.ConfigParser()
    config.read("bot_config.ini")

    bot_token = config.get("Telegram", "token")
    admin_chatid = config.get("Telegram", "admin_chatid")
    spreadsheet_id = config.get("Google", "spreadsheet_id")

    logger.info("Initializing GP bot...")
    app = Application.builder().token(bot_token).build()

    #Commands
    app.add_handler(CommandHandler('start', start_handler))
    app.add_handler(CommandHandler('answer', admin_answer_handler))
    app.add_handler(CallbackQueryHandler(button_action_handler))

    #Messages
    app.add_handler(MessageHandler(filters.TEXT, handle_text_input))

    #Errors
    app.add_error_handler(error)

    logger.info(f"TIME(now): {datetime.now()}")
    logger.info(f"TIME ZONE(tzinfo): {datetime.now().tzinfo}")

    #app.job_queue.run_repeating(send_user_notifications, 5, chat_id=137895506, name="saltru")
    #time for daily jobs - in UTC if tzinfo is None
    #datetime.now() - in local time!

    #DEBUG off all notification jobs!
    #prev-day notifications, UTC=11:00, MSK=14:00
    app.job_queue.run_daily(send_user_notifications, time=time(11, 0, 0))
    #alarms at the day, UTC=6:00, MSK=9:00
    app.job_queue.run_daily(send_user_notifications, time=time(6, 0, 0))

    logger.info("GP bot is running...")
    app.run_polling(poll_interval=1)
