-- E-commerce demo schema for AI Data Pilot PostgreSQL testing.
-- Created by docker-entrypoint-initdb.d on first container start.

DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;

CREATE TABLE products (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    price       NUMERIC(10, 2) NOT NULL,
    stock       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE customers (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    city        TEXT NOT NULL,
    country     TEXT NOT NULL DEFAULT 'Россия',
    segment     TEXT NOT NULL DEFAULT 'regular'
);

CREATE TABLE orders (
    id          SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    product_id  INTEGER NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL,
    total       NUMERIC(12, 2) NOT NULL,
    order_date  DATE NOT NULL,
    status      TEXT NOT NULL DEFAULT 'completed'
);

CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_orders_product ON orders(product_id);
CREATE INDEX idx_orders_customer ON orders(customer_id);

-- 50 products across 5 categories
INSERT INTO products (name, category, price, stock) VALUES
('Ноутбук Pro 14', 'electronics', 89990.00, 25),
('Ноутбук Air 13', 'electronics', 74990.00, 40),
('Смартфон Galaxy', 'electronics', 54990.00, 100),
('Смартфон iPhone', 'electronics', 99990.00, 60),
('Планшет 10', 'electronics', 39990.00, 35),
('Наушники Studio', 'electronics', 24990.00, 80),
('Наушники Pods', 'electronics', 14990.00, 120),
('Часы Smart', 'electronics', 29990.00, 50),
('Монитор 27', 'electronics', 34990.00, 30),
('Клавиатура Mech', 'electronics', 8990.00, 90),
('Футболка хлопок', 'clothing', 1990.00, 200),
('Джинсы classic', 'clothing', 4990.00, 150),
('Куртка зимняя', 'clothing', 12990.00, 60),
('Свитер шерсть', 'clothing', 5990.00, 100),
('Платье вечернее', 'clothing', 8990.00, 45),
('Кроссовки спорт', 'clothing', 6990.00, 80),
('Ботинки кожаные', 'clothing', 11990.00, 50),
('Шапка вязаная', 'clothing', 1490.00, 300),
('Роман Война и мир', 'books', 1290.00, 100),
('Учебник Python', 'books', 2490.00, 150),
('Комикс Marvel', 'books', 890.00, 200),
('Атлас мира', 'books', 3490.00, 40),
('Детская сказка', 'books', 590.00, 250),
('Поваренная книга', 'books', 1790.00, 80),
('Энциклопедия', 'books', 4990.00, 30),
('Кофемашина', 'home', 19990.00, 20),
('Блендер', 'home', 5990.00, 50),
('Тостер', 'home', 3490.00, 70),
('Микроволновка', 'home', 8990.00, 35),
('Пылесос робот', 'home', 24990.00, 25),
('Утюг паровой', 'home', 3990.00, 60),
('Сковорода', 'home', 2990.00, 90),
('Чайник электрич', 'home', 2490.00, 110),
('Мяч футбольный', 'sports', 2990.00, 100),
('Гантели 5кг', 'sports', 1990.00, 80),
('Велосипед горный', 'sports', 29990.00, 20),
('Ракетка теннис', 'sports', 4990.00, 50),
('Коврик йога', 'sports', 1990.00, 120),
('Самокат', 'sports', 8990.00, 40),
('Палатка кемпинг', 'sports', 14990.00, 30),
('Лыжи беговые', 'sports', 18990.00, 25),
('Сноуборд', 'sports', 22990.00, 20),
('Коньки', 'sports', 5990.00, 45),
('Эспандер', 'sports', 990.00, 90),
('Гиря 16кг', 'sports', 2990.00, 40),
('Носки спорт набор', 'clothing', 990.00, 250),
('Ремень кожаный', 'clothing', 2990.00, 80),
('Очки солнечные', 'clothing', 3990.00, 60),
('Зонт складной', 'home', 1490.00, 100),
('Набор ножей', 'home', 6990.00, 30);

-- 100 customers across 8 cities
INSERT INTO customers (name, city, country, segment)
SELECT
    'Клиент ' || i,
    (ARRAY['Москва','Санкт-Петербург','Казань','Екатеринбург','Новосибирск','Краснодар','Самара','Нижний Новгород'])[1 + (i % 8)],
    'Россия',
    (ARRAY['regular','vip','wholesale'])[1 + (i % 3)]
FROM generate_series(1, 100) AS i;

-- ~600 orders spanning Jan-Jul 2026
INSERT INTO orders (customer_id, product_id, quantity, total, order_date, status)
SELECT
    1 + floor(random() * 100)::int,
    1 + floor(random() * 50)::int,
    q,
    q * (SELECT price FROM products WHERE id = 1 + floor(random() * 50)::int),
    DATE '2026-01-01' + floor(random() * 200)::int,
    (ARRAY['completed','completed','completed','completed','cancelled','refunded'])[1 + floor(random() * 6)::int]
FROM (
    SELECT (1 + floor(random() * 4)::int) AS q, generate_series(1, 600) AS seq
) sub;
