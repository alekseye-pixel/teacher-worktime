"""Контроль полноты и целостности учёта без выводов о нарушении трудового режима."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from domain import interval, hours, overlaps


def findings(report, today=None):
    today = today or date.today()
    result = []
    entries = report['entries']
    grouped = defaultdict(list)
    def add(level, kind, entry, message):
        result.append({'level': level, 'kind': kind, 'entry_id': entry.get('id', ''), 'teacher': entry.get('teacher', ''), 'workday': entry.get('workday', ''), 'message': message})
    for entry in entries:
        grouped[(entry['teacher_id'], entry['workday'])].append(entry)
        first, last, duration = interval(entry['start'], entry['end'])
        if duration != entry['minutes']:
            add('Ошибка', 'Продолжительность', entry, 'Минуты не соответствуют началу и окончанию; требуется проверка базы')
        workday = date.fromisoformat(entry['workday'])
        if entry['status'] in {'draft', 'returned'} and workday < today - timedelta(days=7):
            add('Внимание', 'Незавершённая запись', entry, 'Запись старше семи дней не передана на согласование')
        if entry['status'] == 'submitted' and workday < today - timedelta(days=7):
            add('Внимание', 'Ожидает решения', entry, 'Работа выполнена более семи дней назад; ожидается решение руководителя')
        if not entry.get('evidence'):
            add('Сведения', 'Основание', entry, 'Основание не указано; проверьте достаточность описания')
        if duration > 720:
            add('Внимание', 'Длительный интервал', entry, 'Интервал превышает 12 часов; проверьте правильность ввода')
    for rows in grouped.values():
        rows.sort(key=lambda row: row['start'])
        for previous, current in zip(rows, rows[1:]):
            pfirst, plast, _ = interval(previous['start'], previous['end'])
            cfirst, clast, _ = interval(current['start'], current['end'])
            if overlaps(pfirst, plast, cfirst, clast):
                add('Ошибка', 'Пересечение', current, f"Пересечение с записью №{previous['id']}; данные могли быть изменены вне приложения")
        approved = sum(row['minutes'] for row in rows if row['status'] == 'approved')
        if approved > 720:
            add('Внимание', 'Дневная сумма', rows[0], f'За день согласовано {hours(approved)} ч; это сигнал проверки данных, а не вывод о трудовом нарушении')
    for row in report['rows']:
        fake = {'teacher': row['teacher']}
        planned = Decimal(row['plan'])
        fact = Decimal(row['fact'])
        if planned == 0 and fact > 0:
            add('Сведения', 'Факт без плана', fake, 'По виду «' + row['worktype'] + '» есть согласованный факт, но план равен нулю')
        if planned > 0 and fact > planned:
            add('Внимание', 'Превышение плана', fake, 'По виду «' + row['worktype'] + '» факт выше плана; требуется содержательный анализ причин')
    priority = {'Ошибка': 0, 'Внимание': 1, 'Сведения': 2}
    return sorted(result, key=lambda row: (priority[row['level']], row['teacher'], row['workday']))


def calendar(report):
    rows = defaultdict(lambda: {'approved': 0, 'submitted': 0, 'draft': 0, 'returned': 0, 'entries': 0})
    for entry in report['entries']:
        result = rows[entry['workday']]
        result[entry['status']] += entry['minutes']
        result['entries'] += 1
    result = []
    for day, values in sorted(rows.items()):
        result.append({'workday': day, 'approved': hours(values['approved']), 'submitted': hours(values['submitted']), 'draft': hours(values['draft']), 'returned': hours(values['returned']), 'entries': values['entries']})
    return result


def search(report, query, status='', worktype=''):
    needle = str(query).strip().casefold()
    result = []
    for row in report['entries']:
        haystack = ' '.join(str(row.get(key, '')) for key in ('teacher', 'title', 'evidence', 'worktype', 'workday')).casefold()
        if needle and needle not in haystack:
            continue
        if status and row['status'] != status:
            continue
        if worktype and row['type_id'] != int(worktype):
            continue
        result.append(row)
    return result


def export_quality(rows, path):
    import csv
    from domain import csv_safe
    fields = [('level', 'Уровень'), ('kind', 'Проверка'), ('entry_id', 'Запись'), ('teacher', 'Преподаватель'), ('workday', 'Дата'), ('message', 'Замечание')]
    with open(path, 'w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream, delimiter=';')
        writer.writerow([name for key, name in fields])
        for row in rows:
            writer.writerow([csv_safe(row.get(key, '')) for key, name in fields])
