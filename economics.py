"""Воспроизводимый сценарный расчёт. Все исходные показатели являются предположениями проекта."""
from dataclasses import dataclass, asdict
from decimal import Decimal
import json


@dataclass(frozen=True)
class Scenario:
    teachers: int = 20
    days: int = 20
    entries_per_day: int = 6
    entry_before: Decimal = Decimal('2.5')
    entry_after: Decimal = Decimal('1.5')
    review_before: Decimal = Decimal('2.0')
    review_after: Decimal = Decimal('0.8')
    summary_before: Decimal = Decimal('8')
    summary_after: Decimal = Decimal('1')
    teacher_hour: Decimal = Decimal('550')
    reviewer_hour: Decimal = Decimal('650')
    developer_hour: Decimal = Decimal('850')
    developer_hours: Decimal = Decimal('240')
    setup_hours: Decimal = Decimal('12')
    training_teacher_hours: Decimal = Decimal('20')
    training_reviewer_hours: Decimal = Decimal('4')
    training_admin_hours: Decimal = Decimal('2')
    infrastructure_month: Decimal = Decimal('3000')
    support_hours_month: Decimal = Decimal('8')
    discount_rate: Decimal = Decimal('.18')


def calculate(scenario=Scenario(), realization=Decimal('1')):
    s = scenario
    if not Decimal('0') <= realization <= Decimal('1'):
        raise ValueError('Коэффициент реализации должен быть от нуля до единицы')
    count = s.teachers * s.days * s.entries_per_day
    teacher_saved = Decimal(count) * (s.entry_before - s.entry_after) / 60
    reviewer_saved = Decimal(count) * (s.review_before - s.review_after) / 60
    summary_saved = s.summary_before - s.summary_after
    gross_month = teacher_saved * s.teacher_hour + (reviewer_saved + summary_saved) * s.reviewer_hour
    recurring_month = s.infrastructure_month + s.support_hours_month * s.reviewer_hour
    development = s.developer_hours * s.developer_hour
    setup = s.setup_hours * s.reviewer_hour
    training = s.training_teacher_hours * s.teacher_hour + (s.training_reviewer_hours + s.training_admin_hours) * s.reviewer_hour
    initial = development + setup + training
    net_month = gross_month * realization - recurring_month
    annual = net_month * 12
    payback = initial / net_month if net_month > 0 else None
    npv = -initial + sum(annual / ((1 + s.discount_rate) ** year) for year in range(1, 4))
    return {
        'records_month': count,
        'teacher_saved_hours': teacher_saved,
        'reviewer_saved_hours': reviewer_saved,
        'summary_saved_hours': summary_saved,
        'total_saved_hours': teacher_saved + reviewer_saved + summary_saved,
        'gross_month': gross_month,
        'recurring_month': recurring_month,
        'development': development,
        'setup': setup,
        'training': training,
        'initial': initial,
        'realization': realization,
        'net_month': net_month,
        'annual': annual,
        'first_year': annual - initial,
        'payback_months': payback,
        'npv_three_years': npv,
        'break_even_realization': recurring_month / gross_month,
    }


def sensitivity(scenario=Scenario()):
    return [calculate(scenario, value) for value in (Decimal('.25'), Decimal('.50'), Decimal('.75'), Decimal('1'))]


def main():
    result = {'assumptions': asdict(Scenario()), 'base': calculate(), 'sensitivity': sensitivity(),
              'interpretation': 'Сценарная стоимостная оценка высвобождения трудоёмкости. Не фактическая прибыль и не доказательство сокращения расходов МУИВ.'}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
