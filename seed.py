import os

import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:viral@localhost:5432/mcp-test")

DDL = """
-- 1. customers
CREATE TABLE IF NOT EXISTS customers (
    customer_id   SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT UNIQUE NOT NULL,
    country       TEXT NOT NULL,
    joined_at     DATE NOT NULL
);

-- 2. products
CREATE TABLE IF NOT EXISTS products (
    product_id    SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT NOT NULL,
    unit_price    NUMERIC(10,2) NOT NULL
);

-- 3. orders (references customers)
CREATE TABLE IF NOT EXISTS orders (
    order_id      SERIAL PRIMARY KEY,
    customer_id   INT NOT NULL REFERENCES customers(customer_id),
    order_date    DATE NOT NULL,
    status        TEXT NOT NULL  -- 'pending','shipped','delivered','cancelled'
);

-- 4. order_items (references orders + products)
CREATE TABLE IF NOT EXISTS order_items (
    item_id       SERIAL PRIMARY KEY,
    order_id      INT NOT NULL REFERENCES orders(order_id),
    product_id    INT NOT NULL REFERENCES products(product_id),
    quantity      INT NOT NULL,
    unit_price    NUMERIC(10,2) NOT NULL   -- price at time of purchase
);

-- 5. reviews (references customers + products)
CREATE TABLE IF NOT EXISTS reviews (
    review_id     SERIAL PRIMARY KEY,
    customer_id   INT NOT NULL REFERENCES customers(customer_id),
    product_id    INT NOT NULL REFERENCES products(product_id),
    rating        SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    review_date   DATE NOT NULL,
    comment       TEXT
);
"""

SEED = """
-- customers
INSERT INTO customers (name, email, country, joined_at) VALUES
  ('Alice Martin',   'alice@example.com',   'US', '2023-01-15'),
  ('Bob Singh',      'bob@example.com',     'IN', '2023-02-20'),
  ('Clara Zhou',     'clara@example.com',   'CN', '2023-03-05'),
  ('David Osei',     'david@example.com',   'GH', '2023-04-11'),
  ('Eva Müller',     'eva@example.com',     'DE', '2023-05-22'),
  ('Fatima Al-Sayed','fatima@example.com',  'AE', '2023-06-30'),
  ('George Kim',     'george@example.com',  'KR', '2023-07-14'),
  ('Hannah Silva',   'hannah@example.com',  'BR', '2023-08-08'),
  ('Ivan Petrov',    'ivan@example.com',    'RU', '2023-09-19'),
  ('Julia Santos',   'julia@example.com',   'BR', '2023-10-01')
ON CONFLICT DO NOTHING;

-- products
INSERT INTO products (name, category, unit_price) VALUES
  ('Wireless Headphones',   'Electronics',  79.99),
  ('Mechanical Keyboard',   'Electronics', 119.99),
  ('Yoga Mat',              'Fitness',      29.99),
  ('Running Shoes',         'Fitness',      89.99),
  ('Python Cookbook',       'Books',        39.99),
  ('Desk Lamp',             'Home',         24.99),
  ('Coffee Maker',          'Home',         59.99),
  ('Water Bottle',          'Fitness',      14.99),
  ('Noise Cancelling Buds', 'Electronics',  49.99),
  ('Standing Desk',         'Home',        299.99)
ON CONFLICT DO NOTHING;

-- orders (customer_ids 1-10, spread over 2023-2024)
INSERT INTO orders (customer_id, order_date, status) VALUES
  (1,  '2023-11-01', 'delivered'),
  (2,  '2023-11-15', 'delivered'),
  (3,  '2023-12-01', 'shipped'),
  (4,  '2023-12-20', 'delivered'),
  (5,  '2024-01-05', 'delivered'),
  (6,  '2024-01-18', 'cancelled'),
  (7,  '2024-02-10', 'pending'),
  (8,  '2024-02-25', 'delivered'),
  (9,  '2024-03-07', 'shipped'),
  (10, '2024-03-20', 'delivered')
ON CONFLICT DO NOTHING;

-- order_items (2 items per order to give 20 rows total)
INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES
  (1, 1, 1,  79.99), (1, 3, 2,  29.99),
  (2, 2, 1, 119.99), (2, 8, 3,  14.99),
  (3, 4, 1,  89.99), (3, 5, 1,  39.99),
  (4, 6, 2,  24.99), (4, 7, 1,  59.99),
  (5, 9, 1,  49.99), (5, 1, 1,  79.99),
  (6, 10,1, 299.99), (6, 2, 1, 119.99),
  (7, 3, 3,  29.99), (7, 8, 2,  14.99),
  (8, 5, 2,  39.99), (8, 6, 1,  24.99),
  (9, 7, 1,  59.99), (9, 4, 1,  89.99),
  (10,9, 2,  49.99), (10,10,1, 299.99)
ON CONFLICT DO NOTHING;

-- reviews (1 per customer for a product they ordered)
INSERT INTO reviews (customer_id, product_id, rating, review_date, comment) VALUES
  (1,  1, 5, '2023-11-10', 'Amazing sound quality!'),
  (2,  2, 4, '2023-11-20', 'Solid build, great typing feel.'),
  (3,  4, 3, '2023-12-10', 'Good shoes but sizing runs small.'),
  (4,  6, 5, '2023-12-28', 'Perfect desk lamp, love it.'),
  (5,  9, 4, '2024-01-12', 'Great noise cancellation.'),
  (6,  10,2, '2024-01-25', 'Arrived damaged, awaiting refund.'),
  (7,  3, 5, '2024-02-15', 'Best yoga mat I have owned.'),
  (8,  5, 4, '2024-03-02', 'Very practical cookbook.'),
  (9,  7, 3, '2024-03-10', 'Coffee is decent, nothing special.'),
  (10, 9, 5, '2024-03-25', 'Excellent value for the price.')
ON CONFLICT DO NOTHING;
"""

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cur = conn.cursor()

print("Creating tables...")
cur.execute(DDL)
print("Seeding data...")
cur.execute(SEED)
print("Done.")

cur.close()
conn.close()
