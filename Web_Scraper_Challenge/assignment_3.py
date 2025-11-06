# monthly_revenue → yearly_revenue 계산 함수
# Takes monthly revenue and returns yearly revenue
def get_yearly_revenue(monthly_revenue):
    return monthly_revenue * 12


# monthly_expenses → yearly_expenses 계산 함수
# Takes monthly expenses and returns yearly expenses
def get_yearly_expenses(monthly_expenses):
    return monthly_expenses * 12


# profit → tax_amount 계산 함수
# Takes profit and returns tax amount using conditional tax rate
def get_tax_amount(profit):
    # 만약 이익이 1,000,000 초과이면 세율 25%, 아니면 15%
    if profit > 1_000_000:
        tax_rate = 0.25
    else:
        tax_rate = 0.15
    return profit * tax_rate


# tax_amount, tax_credits → 세액공제 금액 계산 함수
# Takes tax amount and credit rate, returns discount amount
def apply_tax_credits(tax_amount, tax_credits):
    # 세금 금액에 공제율을 곱함
    return tax_amount * tax_credits


# === 입력 데이터 / Input Data ===
monthly_revenue = 5_500_000  # 월간 매출 (Monthly Revenue)
monthly_expenses = 2_700_000  # 월간 비용 (Monthly Expenses)
tax_credits = 0.01  # 세액 공제율 (1% tax credit)

# === 연간 수익 및 비용 계산 / Calculate yearly revenue and expenses ===
yearly_revenue = get_yearly_revenue(monthly_revenue)  # 연간 매출
yearly_expenses = get_yearly_expenses(monthly_expenses)  # 연간 비용

# === 이익 계산 / Calculate profit ===
profit = yearly_revenue - yearly_expenses  # 이익 = 매출 - 비용

# === 세금 계산 / Calculate tax ===
tax_amount = get_tax_amount(profit)  # 이익에 따른 세금 금액 계산

# === 세액 공제 적용 / Apply tax credits ===
final_tax_amount = tax_amount - apply_tax_credits(
    tax_amount, tax_credits
)  # 공제 후 최종 세금

# === 결과 출력 / Print final tax amount ===
print(f"Your tax bill is: ${final_tax_amount:,}")  # 천 단위 쉼표 추가
