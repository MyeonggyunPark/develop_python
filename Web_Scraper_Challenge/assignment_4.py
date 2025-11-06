import time

# 계산기 프로그램 시작 / Calculator Program Start
print("\n[ CALCULATOR ]\n")


# 정수 입력 함수 / Integer Input Function
# 정수가 아닌 값에 대한 예외 처리 / Exception handling for non-integer input
def get_integer(prompt):
    while True:
        try:
            return int(input(prompt))
        except ValueError:
            print("\n❌ Invalid input! Please enter an integer!\n")
            print("Loding...")
            time.sleep(1)


# 메인 루프 시작 / Main Loop Start
while True:

    # 두 개의 정수 입력 받기 / Get two integers from user
    num1 = get_integer("Input a number --> ")
    num2 = get_integer("Input another number --> ")

    # 연산 옵션 출력 / Display operation menu
    print(
        """
    ---- [ OPERATION ] ----\n
        [1] ➕\t  [2] ➖\n
        [3] ✖️\t  [4] ➗\n
        [5] 🛑 (EXIT)
    """
    )

    # match-case를 이용한 연산 분기 / Perform operation based on user choice
    option_input = int(input("Choose a number for operation --> "))
    match option_input:
        case 1:
            result = num1 + num2
        case 2:
            result = num1 - num2
        case 3:
            result = num1 * num2
        case 4:
            # 0으로 나누기 예외 처리 / Division by zero error handling
            if num2 == 0:
                print("\n❌ You cannot divide by zero!\n")
                print("Loding...")
                time.sleep(1)
                continue
            else:
                result = num1 / num2

        # 프로그램 종료 처리 / Exit program
        case 5:
            print("🛑 Exiting calculator")
            break
        case _:
            # 잘못된 입력 처리 / Invalid input handling
            print("\n❌ Invalid option! Please choose a valid number.\n")
            continue
    
    # 결과 출력 / Display result    
    print(f"\n🟩 (RESULT: {result})\n")
