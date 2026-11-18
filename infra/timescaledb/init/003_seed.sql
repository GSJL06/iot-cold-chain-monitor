-- Flota sintética: 3 sitios y 12 dispositivos (docs/architecture.md, sección 3).
-- Rangos por clase: REFRIGERATED 2–8 °C, FROZEN −25..−15 °C, ULTRA_FROZEN −90..−60 °C.
-- Todos los datos son inventados.

INSERT INTO sites (site_id, name, city) VALUES
    ('hosp-a', 'Hospital A (sintético)',    'Madrid'),
    ('farm-b', 'Farmacia B (sintética)',    'Valencia'),
    ('lab-c',  'Laboratorio C (sintético)', 'Barcelona');

INSERT INTO devices (device_id, site_id, label, storage_class, min_c, max_c, fw_version) VALUES
    ('SIM-HA-001', 'hosp-a', 'Nevera vacunas 1',     'REFRIGERATED',  2.0,   8.0, 'sim-1.0.0'),
    ('SIM-HA-002', 'hosp-a', 'Nevera vacunas 2',     'REFRIGERATED',  2.0,   8.0, 'sim-1.0.0'),
    ('SIM-HA-003', 'hosp-a', 'Nevera reactivos',     'REFRIGERATED',  2.0,   8.0, 'sim-1.0.0'),
    ('SIM-HA-004', 'hosp-a', 'Congelador plasma',    'FROZEN',      -25.0, -15.0, 'sim-1.0.0'),
    ('SIM-FB-001', 'farm-b', 'Nevera insulinas',     'REFRIGERATED',  2.0,   8.0, 'sim-1.0.0'),
    ('SIM-FB-002', 'farm-b', 'Nevera biológicos',    'REFRIGERATED',  2.0,   8.0, 'sim-1.0.0'),
    ('SIM-FB-003', 'farm-b', 'Nevera mostrador',     'REFRIGERATED',  2.0,   8.0, 'sim-1.0.0'),
    ('SIM-FB-004', 'farm-b', 'Congelador reserva',   'FROZEN',      -25.0, -15.0, 'sim-1.0.0'),
    ('SIM-LC-001', 'lab-c',  'Nevera muestras',      'REFRIGERATED',  2.0,   8.0, 'sim-1.0.0'),
    ('SIM-LC-002', 'lab-c',  'Congelador reactivos', 'FROZEN',      -25.0, -15.0, 'sim-1.0.0'),
    ('SIM-LC-003', 'lab-c',  'Ultracongelador 1',    'ULTRA_FROZEN', -90.0, -60.0, 'sim-1.0.0'),
    ('SIM-LC-004', 'lab-c',  'Ultracongelador 2',    'ULTRA_FROZEN', -90.0, -60.0, 'sim-1.0.0');

INSERT INTO device_status (device_id, state)
SELECT device_id, 'offline' FROM devices;
