-- Scrooge database, migration 0010: the taxonomy's categories take English names
-- (decided 2026-11-24).
--
-- The app is in English; its categories were Italian. Each category of the built-in taxonomy
-- is renamed where it is, so the transactions and the overrides that carry it (by id) follow.
-- An expense category is renamed only under the group it belongs to, and the groups after
-- them, so a category someone added under a group of the taxonomy keeps its name and follows
-- the group. A category someone added on their own is left as it is. The rules files and
-- categories.yaml are not touched: what they name in Italian is read as the English name
-- (categories.ITALIAN).

-- The expense categories, under their groups.
UPDATE categories SET name = 'Rent'
WHERE side = 'uscite' AND name = 'Affitto'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Casa'
  );
UPDATE categories SET name = 'Utilities'
WHERE side = 'uscite' AND name = 'Utenze'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Casa'
  );
UPDATE categories SET name = 'Household'
WHERE side = 'uscite' AND name = 'Arredamento e casa'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Casa'
  );
UPDATE categories SET name = 'Health insurance'
WHERE side = 'uscite' AND name = 'Assicurazione sanitaria'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Salute'
  );
UPDATE categories SET name = 'Dentist'
WHERE side = 'uscite' AND name = 'Dentista'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Salute'
  );
UPDATE categories SET name = 'Doctor/Pharmacy'
WHERE side = 'uscite' AND name = 'Medico/Farmacia'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Salute'
  );
UPDATE categories SET name = 'Groceries'
WHERE side = 'uscite' AND name = 'Spesa'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Alimentari'
  );
UPDATE categories SET name = 'Eating out'
WHERE side = 'uscite' AND name = 'Ristoranti e bar'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Ristoranti e bar'
  );
UPDATE categories SET name = 'Trains'
WHERE side = 'uscite' AND name = 'Treni'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Trasporti'
  );
UPDATE categories SET name = 'Fuel'
WHERE side = 'uscite' AND name = 'Carburante'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Trasporti'
  );
UPDATE categories SET name = 'Local transport'
WHERE side = 'uscite' AND name = 'Trasporti locali'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Trasporti'
  );
UPDATE categories SET name = 'Flights'
WHERE side = 'uscite' AND name = 'Voli'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Trasporti'
  );
UPDATE categories SET name = 'Parking and tolls'
WHERE side = 'uscite' AND name = 'Parcheggi e pedaggi'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Trasporti'
  );
UPDATE categories SET name = 'Digital subscriptions'
WHERE side = 'uscite' AND name = 'Abbonamenti digitali'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Abbonamenti'
  );
UPDATE categories SET name = 'Barber'
WHERE side = 'uscite' AND name = 'Barbiere'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Cura personale'
  );
UPDATE categories SET name = 'Clothes'
WHERE side = 'uscite' AND name = 'Vestiti'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Abbigliamento'
  );
UPDATE categories SET name = 'Electronics'
WHERE side = 'uscite' AND name = 'Elettronica'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Acquisti'
  );
UPDATE categories SET name = 'Other shopping'
WHERE side = 'uscite' AND name = 'Acquisti vari'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Acquisti'
  );
UPDATE categories SET name = 'Accommodation'
WHERE side = 'uscite' AND name = 'Alloggi'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Viaggi e svago'
  );
UPDATE categories SET name = 'Activities'
WHERE side = 'uscite' AND name = 'Attività/Svago'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Viaggi e svago'
  );
UPDATE categories SET name = 'Language lessons'
WHERE side = 'uscite' AND name = 'Lezioni di lingua'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Formazione'
  );
UPDATE categories SET name = 'Books'
WHERE side = 'uscite' AND name = 'Libri'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Formazione'
  );
UPDATE categories SET name = 'Gifts'
WHERE side = 'uscite' AND name = 'Regali'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Regali'
  );
UPDATE categories SET name = 'Donations'
WHERE side = 'uscite' AND name = 'Donazioni'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Regali'
  );
UPDATE categories SET name = 'Taxes'
WHERE side = 'uscite' AND name = 'Tasse'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Tasse e burocrazia'
  );
UPDATE categories SET name = 'Paperwork'
WHERE side = 'uscite' AND name = 'Documenti e burocrazia'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Tasse e burocrazia'
  );
UPDATE categories SET name = 'Fees'
WHERE side = 'uscite' AND name = 'Commissioni'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Banca e broker'
  );
UPDATE categories SET name = 'Withholding tax'
WHERE side = 'uscite' AND name = 'Ritenute'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Banca e broker'
  );
UPDATE categories SET name = 'Investments'
WHERE side = 'uscite' AND name = 'Investimenti'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Investimenti'
  );
UPDATE categories SET name = 'Uncategorized'
WHERE side = 'uscite' AND name = 'Non classificato'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Non classificato'
  );

-- The groups.
UPDATE categories SET name = 'Home'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Casa';
UPDATE categories SET name = 'Health'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Salute';
UPDATE categories SET name = 'Food'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Alimentari';
UPDATE categories SET name = 'Eating out'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Ristoranti e bar';
UPDATE categories SET name = 'Transport'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Trasporti';
UPDATE categories SET name = 'Phone'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Telefonia';
UPDATE categories SET name = 'Subscriptions'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Abbonamenti';
UPDATE categories SET name = 'Personal care'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Cura personale';
UPDATE categories SET name = 'Clothing'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Abbigliamento';
UPDATE categories SET name = 'Shopping'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Acquisti';
UPDATE categories SET name = 'Travel and leisure'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Viaggi e svago';
UPDATE categories SET name = 'Education'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Formazione';
UPDATE categories SET name = 'Gifts'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Regali';
UPDATE categories SET name = 'Taxes and paperwork'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Tasse e burocrazia';
UPDATE categories SET name = 'Banking'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Banca e broker';
UPDATE categories SET name = 'Investments'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Investimenti';
UPDATE categories SET name = 'Uncategorized'
WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Non classificato';

-- Income and transfers, which have no groups.
UPDATE categories SET name = 'Salary'
WHERE side = 'entrate' AND parent_id IS NULL AND name = 'Stipendio';
UPDATE categories SET name = 'Dividends'
WHERE side = 'entrate' AND parent_id IS NULL AND name = 'Dividendi';
UPDATE categories SET name = 'Interest'
WHERE side = 'entrate' AND parent_id IS NULL AND name = 'Interessi';
UPDATE categories SET name = 'Refunds'
WHERE side = 'entrate' AND parent_id IS NULL AND name = 'Rimborsi';
UPDATE categories SET name = 'Gifts received'
WHERE side = 'entrate' AND parent_id IS NULL AND name = 'Regali ricevuti';
UPDATE categories SET name = 'Fees'
WHERE side = 'entrate' AND parent_id IS NULL AND name = 'Commissioni';
UPDATE categories SET name = 'Investments'
WHERE side = 'entrate' AND parent_id IS NULL AND name = 'Investimenti';
UPDATE categories SET name = 'Uncategorized'
WHERE side = 'entrate' AND parent_id IS NULL AND name = 'Non classificato';
UPDATE categories SET name = 'Revolut top-up'
WHERE side = 'transfer' AND parent_id IS NULL AND name = 'Topup Revolut';
UPDATE categories SET name = 'IBKR top-up'
WHERE side = 'transfer' AND parent_id IS NULL AND name = 'Topup IBKR';
UPDATE categories SET name = 'Buy EUR'
WHERE side = 'transfer' AND parent_id IS NULL AND name = 'Acquisto EUR';
UPDATE categories SET name = 'Buy USD'
WHERE side = 'transfer' AND parent_id IS NULL AND name = 'Acquisto USD';
UPDATE categories SET name = 'Withdrawals'
WHERE side = 'transfer' AND parent_id IS NULL AND name = 'Prelievi';
UPDATE categories SET name = 'Own transfer'
WHERE side = 'transfer' AND parent_id IS NULL AND name = 'Giroconto';
