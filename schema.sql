CREATE TABLE IF NOT EXISTS cars (
    id          VARCHAR(20) PRIMARY KEY,
    name        VARCHAR(120) NOT NULL,
    car_type    VARCHAR(50)  NOT NULL,
    price       NUMERIC(14, 0) NOT NULL,
    video_url   TEXT,
    stock       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS customers (
    id              VARCHAR(20) PRIMARY KEY,
    session_id      VARCHAR(64) UNIQUE NOT NULL,
    name            VARCHAR(120),
    phone           VARCHAR(20),
    email           VARCHAR(120),
    owned_car_model VARCHAR(120),
    owned_car_km    INTEGER,
    budget          NUMERIC(14, 0),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS maintenance_items (
    id              SERIAL PRIMARY KEY,
    model           VARCHAR(120) NOT NULL,
    km_milestone    INTEGER NOT NULL,
    items           TEXT[] NOT NULL,
    estimated_cost  NUMERIC(14, 0) NOT NULL,
    UNIQUE (model, km_milestone)
);

CREATE TABLE IF NOT EXISTS discount_requests (
    id                  VARCHAR(20) PRIMARY KEY,
    session_id          VARCHAR(64) NOT NULL,
    car_id              VARCHAR(20) NOT NULL REFERENCES cars(id),
    requested_percent   NUMERIC(5, 2) NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_discount_session ON discount_requests(session_id);

CREATE TABLE IF NOT EXISTS maintenance_schedule (
    id              VARCHAR(20) PRIMARY KEY,
    session_id      VARCHAR(64) NOT NULL,
    customer_id     VARCHAR(20) REFERENCES customers(id),
    car_id          VARCHAR(20) REFERENCES cars(id),
    due_date        TIMESTAMPTZ NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'SCHEDULED',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id          SERIAL PRIMARY KEY,
    session_id  VARCHAR(64) NOT NULL,
    role        VARCHAR(20) NOT NULL,
    content     TEXT NOT NULL,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);

INSERT INTO cars (id, name, car_type, price, video_url, stock) VALUES
    ('C01', 'Toyota Vios G',        'Sedan', 592000000,  'https://youtube.com/vios',  1),
    ('C02', 'Toyota Corolla Cross', 'SUV',   760000000,  'https://youtube.com/cross', 1),
    ('C03', 'Toyota Camry 2.5Q',    'Sedan', 1405000000, NULL,                        1)
ON CONFLICT (id) DO NOTHING;

INSERT INTO maintenance_items (model, km_milestone, items, estimated_cost)
SELECT c.name, m.km_milestone, m.items, m.estimated_cost
FROM cars c
CROSS JOIN (
    VALUES
        (5000,  ARRAY['Thay dầu máy', 'Kiểm tra lọc gió', 'Siết ốc gầm', 'Kiểm tra phanh'], 1200000),
        (10000, ARRAY['Thay dầu máy', 'Kiểm tra lọc gió', 'Siết ốc gầm', 'Kiểm tra phanh'], 1200000),
        (20000, ARRAY['Thay dầu máy', 'Thay lọc dầu', 'Vệ sinh phanh 4 bánh', 'Đảo lốp'], 3500000),
        (40000, ARRAY['Bảo dưỡng cấp lớn: Thay dầu máy, dầu phanh, lọc xăng, bugi'], 4500000)
) AS m(km_milestone, items, estimated_cost)
ON CONFLICT (model, km_milestone) DO NOTHING;