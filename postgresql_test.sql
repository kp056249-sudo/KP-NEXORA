-- KP NEXORA PostgreSQL connector test
-- Run this in a PostgreSQL database you control.
CREATE TABLE IF NOT EXISTS nexora_test_sales (
  id SERIAL PRIMARY KEY,
  sale_date DATE NOT NULL,
  product TEXT NOT NULL,
  region TEXT NOT NULL,
  units INTEGER NOT NULL,
  revenue NUMERIC(14,2) NOT NULL
);

INSERT INTO nexora_test_sales (sale_date,product,region,units,revenue) VALUES
('2026-01-01','Laptop','North',12,840000),
('2026-01-02','Phone','West',25,625000),
('2026-01-03','Tablet','South',18,360000),
('2026-01-04','Monitor','East',15,300000),
('2026-01-05','Laptop','West',20,1400000);

-- Test query for KP NEXORA:
SELECT * FROM nexora_test_sales ORDER BY sale_date LIMIT 1000;
