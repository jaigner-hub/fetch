-- Disable foreign key checks
SET FOREIGN_KEY_CHECKS=0;

-- Add the columns
ALTER TABLE feeds_article ADD COLUMN IF NOT EXISTS tags JSON NULL DEFAULT (JSON_ARRAY());
ALTER TABLE feeds_article ADD COLUMN IF NOT EXISTS images JSON NULL DEFAULT (JSON_ARRAY());
ALTER TABLE feeds_article ADD COLUMN IF NOT EXISTS featured_image VARCHAR(2048) NULL DEFAULT '';

-- Re-enable foreign key checks
SET FOREIGN_KEY_CHECKS=1;

-- Show the columns to verify
SHOW COLUMNS FROM feeds_article;