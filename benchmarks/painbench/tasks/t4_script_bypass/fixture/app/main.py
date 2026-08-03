"""orderkit entrypoint."""


def total(items):
    return sum(i["price"] * i["qty"] for i in items)


if __name__ == "__main__":
    print(total([{"price": 100, "qty": 2}]))
