from app.auth import login


def main() -> None:
    result = login("Alice", "wonderland")
    print(result)


if __name__ == "__main__":
    main()
