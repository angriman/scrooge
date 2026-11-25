import { describe, expect, it } from 'vitest';
import { droppedFiles, hidden, shown } from './dropped.js';

/** A file of a drop, as the browser gives it. */
function file(name: string) {
	const made = (done: (f: File) => void) => done(new File(['x'], name));
	return { name, isFile: true, isDirectory: false, file: made };
}

/** A folder whose reader gives two entries at a time, then an empty part. */
function folder(name: string, entries: object[]) {
	return {
		name,
		isFile: false,
		isDirectory: true,
		createReader() {
			let at = 0;
			return {
				readEntries(done: (part: object[]) => void) {
					done(entries.slice(at, at + 2));
					at += 2;
				}
			};
		}
	};
}

/** A drop of `entries`; `null` is an item the browser gives no entry for. */
function drop(entries: (object | null)[], files: File[] = []): DataTransfer {
	const items = entries.map((entry) => ({ kind: 'file', webkitGetAsEntry: () => entry }));
	return { items, files } as unknown as DataTransfer;
}

const names = (files: File[]) => files.map((f) => f.name);

describe('the files of a drop', () => {
	it('are the files dropped and those of the folders, at any depth', async () => {
		const year = folder('2026', [
			file('a.csv'),
			file('b.csv'),
			file('c.pdf'),
			folder('Revolut', [file('d.tsv')])
		]);
		expect(names(await droppedFiles(drop([file('loose.csv'), year])))).toEqual([
			'loose.csv',
			'a.csv',
			'b.csv',
			'c.pdf',
			'd.tsv'
		]);
	});

	it('leave out hidden files and folders and __MACOSX', async () => {
		const all = folder('statements', [
			file('.DS_Store'),
			file('a.csv'),
			folder('.git', [file('config')]),
			folder('__MACOSX', [file('._a.csv')])
		]);
		expect(names(await droppedFiles(drop([all, file('.hidden')])))).toEqual(['a.csv']);
		expect(names(await droppedFiles(drop([folder('empty', [])])))).toEqual([]);
	});

	it('are the files as they are without entries', async () => {
		const files = [new File(['x'], 'a.csv')];
		expect(await droppedFiles(drop([null], files))).toEqual(files);
		expect(await droppedFiles(drop([], files))).toEqual(files);
	});
});

describe('what is hidden', () => {
	it('is a name with a dot first, or __MACOSX', () => {
		expect([hidden('.DS_Store'), hidden('__MACOSX'), hidden('a.csv')]).toEqual([
			true,
			true,
			false
		]);
	});

	it('is a file chosen with its folder with something hidden on its path', () => {
		const at = (path: string) => {
			const chosen = new File(['x'], path.split('/').at(-1)!);
			Object.defineProperty(chosen, 'webkitRelativePath', { value: path });
			return chosen;
		};
		expect(shown(at('statements/a.csv'))).toBe(true);
		expect(shown(at('statements/.DS_Store'))).toBe(false);
		expect(shown(at('statements/__MACOSX/a.csv'))).toBe(false);
		expect(shown(new File(['x'], 'a.csv'))).toBe(true);
	});
});
