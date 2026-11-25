-- An account closed on a day (decided 2026-11-25, plan decision 51): a bank account closed, a
-- pension fund left. It held nothing at the end of that day and nothing is booked, stated or
-- typed for it after; it keeps its history, and the pages list it apart from the open ones.
ALTER TABLE accounts ADD COLUMN closed_on TEXT CHECK (closed_on IS date(closed_on));
