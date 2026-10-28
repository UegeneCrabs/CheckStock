PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

CREATE TABLE fulfillments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );

CREATE TABLE ff_warehouse_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            fulfillment TEXT NOT NULL,
            wb_warehouse_id INTEGER NOT NULL,
            wb_warehouse_name TEXT NOT NULL,
            updated_at TEXT,
            UNIQUE(store_slug, fulfillment)
        );

CREATE TABLE mp_schemes (
	id INTEGER NOT NULL, 
	store_slug VARCHAR NOT NULL, 
	marketplace VARCHAR NOT NULL, 
	scheme VARCHAR NOT NULL, 
	label VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (store_slug, marketplace, scheme)
);

CREATE TABLE mp_warehouse_cluster (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marketplace TEXT NOT NULL,
            warehouse TEXT NOT NULL,
            cluster TEXT NOT NULL,
            updated_at TEXT,
            UNIQUE(marketplace, warehouse)
        );

CREATE TABLE ff_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            article TEXT NOT NULL,
            fulfillment TEXT NOT NULL,
            marketplace TEXT NOT NULL DEFAULT 'WB',
            quantity INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            UNIQUE(store_slug, article, fulfillment, marketplace)
        );

CREATE TABLE mp_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            article TEXT NOT NULL,
            marketplace TEXT NOT NULL,
            scheme TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            UNIQUE(store_slug, article, marketplace, scheme)
        );

CREATE TABLE mp_warehouse_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            article TEXT NOT NULL,
            marketplace TEXT NOT NULL,
            scheme TEXT NOT NULL,
            warehouse TEXT NOT NULL,
            cluster TEXT,
            quantity INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            UNIQUE(store_slug, article, marketplace, scheme, warehouse)
        );

CREATE TABLE trash_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            article TEXT NOT NULL,
            marketplace TEXT NOT NULL,
            fulfillment TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT, checked INTEGER NOT NULL DEFAULT 0,
            UNIQUE(store_slug, article, marketplace, fulfillment)
        );

CREATE TABLE fulfillment_stock_daily_history (
	store_slug VARCHAR NOT NULL, 
	marketplace VARCHAR NOT NULL, 
	article VARCHAR NOT NULL, 
	fulfillment VARCHAR NOT NULL, 
	day VARCHAR NOT NULL, 
	quantity INTEGER DEFAULT '0' NOT NULL, 
	captured_at VARCHAR NOT NULL, 
	PRIMARY KEY (store_slug, marketplace, article, fulfillment, day)
);

CREATE TABLE marketplace_stock_daily_history (
	store_slug VARCHAR NOT NULL, 
	marketplace VARCHAR NOT NULL, 
	article VARCHAR NOT NULL, 
	scheme VARCHAR NOT NULL, 
	day VARCHAR NOT NULL, 
	quantity INTEGER DEFAULT '0' NOT NULL, 
	captured_at VARCHAR NOT NULL, 
	PRIMARY KEY (store_slug, marketplace, article, scheme, day)
);

CREATE TABLE ff_transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            article TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            from_fulfillment TEXT NOT NULL,
            from_marketplace TEXT NOT NULL,
            to_fulfillment TEXT NOT NULL,
            to_marketplace TEXT NOT NULL,
            user_id INTEGER,
            user_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

CREATE TABLE ff_transit_batches (
	id INTEGER NOT NULL, 
	store_slug VARCHAR NOT NULL, 
	from_fulfillment VARCHAR NOT NULL, 
	from_marketplace VARCHAR NOT NULL, 
	to_fulfillment VARCHAR NOT NULL, 
	to_marketplace VARCHAR NOT NULL, 
	status VARCHAR DEFAULT 'in_transit' NOT NULL, 
	note TEXT DEFAULT '' NOT NULL, 
	sent_by_user_id INTEGER, 
	sent_by_name VARCHAR NOT NULL, 
	sent_at VARCHAR NOT NULL, 
	last_received_by_user_id INTEGER, 
	last_received_by_name VARCHAR, 
	last_received_at VARCHAR, 
	cancelled_by_user_id INTEGER, 
	cancelled_by_name VARCHAR, 
	cancelled_at VARCHAR, 
	cancellation_reason TEXT, kind VARCHAR NOT NULL DEFAULT 'ff_transfer', 
	PRIMARY KEY (id)
);

CREATE TABLE ff_transit_items (
	id INTEGER NOT NULL, 
	batch_id INTEGER NOT NULL, 
	from_article VARCHAR NOT NULL, 
	to_article VARCHAR NOT NULL, 
	barcode VARCHAR, 
	name VARCHAR, 
	sent_quantity INTEGER NOT NULL, 
	received_quantity INTEGER DEFAULT '0' NOT NULL, 
	cancelled_quantity INTEGER DEFAULT '0' NOT NULL, 
	purchase_price FLOAT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(batch_id) REFERENCES ff_transit_batches (id) ON DELETE CASCADE
);

CREATE TABLE ff_transit_receipts (
	id INTEGER NOT NULL, 
	batch_id INTEGER NOT NULL, 
	user_id INTEGER, 
	user_name VARCHAR NOT NULL, 
	note TEXT DEFAULT '' NOT NULL, 
	received_at VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(batch_id) REFERENCES ff_transit_batches (id) ON DELETE CASCADE
);

CREATE TABLE ff_transit_receipt_items (
	id INTEGER NOT NULL, 
	receipt_id INTEGER NOT NULL, 
	transit_item_id INTEGER NOT NULL, 
	quantity INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(receipt_id) REFERENCES ff_transit_receipts (id) ON DELETE CASCADE, 
	FOREIGN KEY(transit_item_id) REFERENCES ff_transit_items (id) ON DELETE CASCADE
);

CREATE TABLE ff_stock_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            fulfillment TEXT NOT NULL,
            source_type TEXT NOT NULL,
            sheet_url TEXT,
            table_title TEXT NOT NULL,
            total_rows INTEGER NOT NULL DEFAULT 0,
            matched INTEGER NOT NULL DEFAULT 0,
            unmatched INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        , marketplace TEXT NOT NULL DEFAULT 'WB');

CREATE TABLE ff_import_snapshots (
	id INTEGER NOT NULL, 
	store_slug VARCHAR NOT NULL, 
	fulfillment VARCHAR NOT NULL, 
	marketplace VARCHAR NOT NULL, 
	source_type VARCHAR NOT NULL, 
	source_key VARCHAR NOT NULL, 
	article VARCHAR NOT NULL, 
	quantity INTEGER DEFAULT '0' NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (store_slug, fulfillment, marketplace, source_type, source_key, article)
);

CREATE TABLE inbound_supply_snapshots (
	store_slug VARCHAR NOT NULL, 
	marketplace VARCHAR NOT NULL, 
	payload_json TEXT DEFAULT '[]' NOT NULL, 
	status VARCHAR DEFAULT 'never' NOT NULL, 
	last_attempt VARCHAR, 
	last_success VARCHAR, 
	last_finished VARCHAR, 
	error TEXT DEFAULT '' NOT NULL, 
	run_token VARCHAR, 
	lease_until VARCHAR, 
	PRIMARY KEY (store_slug, marketplace)
);

CREATE TABLE manual_supplies (
	id INTEGER NOT NULL, 
	delivery_at VARCHAR NOT NULL, 
	origin VARCHAR NOT NULL, 
	destination VARCHAR NOT NULL, 
	supply_type VARCHAR NOT NULL, 
	ready INTEGER DEFAULT '0' NOT NULL, 
	created_by_user_id INTEGER, 
	created_by_name VARCHAR NOT NULL, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, store_slug TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', 
	PRIMARY KEY (id)
);

CREATE TABLE supply_arrivals_snapshots (
	source_key VARCHAR NOT NULL, 
	sheet_title VARCHAR NOT NULL, 
	payload_json TEXT NOT NULL, 
	last_attempt VARCHAR, 
	last_success VARCHAR, 
	error TEXT NOT NULL, 
	PRIMARY KEY (source_key)
);

CREATE TABLE supply_arrival_stock (
	source_key VARCHAR NOT NULL, 
	supply_id VARCHAR(64) NOT NULL, 
	checked INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	marked_at VARCHAR, 
	marked_by VARCHAR, 
	marked_by_id INTEGER, 
	PRIMARY KEY (source_key, supply_id)
);

CREATE TABLE fbo_supply_movements (
	store_slug VARCHAR NOT NULL, 
	marketplace VARCHAR NOT NULL, 
	supply_key VARCHAR NOT NULL, 
	payload_json TEXT NOT NULL, 
	PRIMARY KEY (store_slug, marketplace, supply_key)
);

CREATE TABLE fbo_supply_batches (
	batch_id INTEGER NOT NULL, 
	store_slug VARCHAR NOT NULL, 
	marketplace VARCHAR NOT NULL, 
	supply_key VARCHAR NOT NULL, 
	fulfillment VARCHAR NOT NULL, 
	PRIMARY KEY (batch_id), 
	FOREIGN KEY(batch_id) REFERENCES ff_transit_batches (id)
);

CREATE TABLE stock_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            kind TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_name TEXT,
            sheet_url TEXT,
            from_fulfillment TEXT,
            from_marketplace TEXT,
            to_fulfillment TEXT,
            to_marketplace TEXT,
            user_id INTEGER,
            user_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        , note TEXT, transit_batch_id INTEGER);

CREATE TABLE stock_operation_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id INTEGER NOT NULL,
            article TEXT NOT NULL,
            barcode TEXT,
            name TEXT,
            quantity INTEGER NOT NULL
        , purchase_price FLOAT, purchase_price_recorded INTEGER NOT NULL DEFAULT 0);

CREATE TABLE stock_operation_report_flags (
	operation_id INTEGER NOT NULL, 
	is_fbs_transfer INTEGER DEFAULT '0' NOT NULL, 
	updated_by INTEGER, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (operation_id), 
	FOREIGN KEY(operation_id) REFERENCES stock_operations (id) ON DELETE CASCADE
);

CREATE TABLE stock_audit_randomizations (
	id INTEGER NOT NULL, 
	batch_key VARCHAR(32) NOT NULL, 
	month_key VARCHAR(7) NOT NULL, 
	fulfillment VARCHAR NOT NULL, 
	store_slug VARCHAR NOT NULL, 
	article VARCHAR NOT NULL, 
	ff_quantity INTEGER NOT NULL, 
	fbs_quantity INTEGER NOT NULL, 
	user_id INTEGER, 
	user_name VARCHAR NOT NULL, 
	generated_at VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (month_key, store_slug, article)
);

CREATE TABLE stock_mutation_requests (
	store_slug VARCHAR(200) NOT NULL, 
	user_id INTEGER NOT NULL, 
	request_key VARCHAR(128) NOT NULL, 
	payload_hash VARCHAR(64) NOT NULL, 
	response_json TEXT, 
	status_code INTEGER, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (store_slug, user_id, request_key)
);

CREATE TABLE used_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            kind TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            label TEXT NOT NULL,
            source_type TEXT NOT NULL,
            operation_id INTEGER,
            user_name TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(store_slug, kind, fingerprint)
        );

CREATE INDEX idx_ff_stock_daily_lookup ON fulfillment_stock_daily_history (store_slug, marketplace, article, day);

CREATE INDEX idx_mp_stock_daily_lookup ON marketplace_stock_daily_history (store_slug, marketplace, article, day);

CREATE INDEX idx_ff_transit_store_status ON ff_transit_batches (store_slug, status, id);

CREATE INDEX idx_ff_transit_items_batch ON ff_transit_items (batch_id, id);

CREATE INDEX idx_ff_transit_receipts_batch ON ff_transit_receipts (batch_id, id);

CREATE INDEX idx_manual_supplies_delivery_at ON manual_supplies (delivery_at, id);

CREATE INDEX ix_fbo_supply_batches_store_slug ON fbo_supply_batches (store_slug);

CREATE INDEX idx_stock_audit_randomizations_month ON stock_audit_randomizations (month_key, fulfillment);

CREATE INDEX idx_stock_audit_randomizations_batch ON stock_audit_randomizations (batch_key);

COMMIT;
