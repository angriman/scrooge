/**
 * The files of a drop on the import page: the files dropped, and every file inside the
 * folders dropped, at any depth. Hidden files and folders (a name starting with a dot) and
 * the `__MACOSX` folder a Mac puts in its zips are left out.
 */

/** Whether a file or folder of that name is left out of a folder. */
export function hidden(name: string): boolean {
	return name.startsWith('.') || name === '__MACOSX';
}

/** Whether a file chosen with its folder is taken: nothing on its path is hidden. */
export function shown(file: File): boolean {
	return !(file.webkitRelativePath || file.name).split('/').some(hidden);
}

/** Everything in a folder. Its reader gives a part at a time, and an empty part at the end. */
async function children(folder: FileSystemDirectoryEntry): Promise<FileSystemEntry[]> {
	const reader = folder.createReader();
	const all: FileSystemEntry[] = [];
	for (;;) {
		const part = await new Promise<FileSystemEntry[]>((resolve, reject) =>
			reader.readEntries(resolve, reject)
		);
		if (!part.length) return all;
		all.push(...part);
	}
}

/** The files of an entry: itself, or what its folder holds. */
export async function filesOf(entry: FileSystemEntry): Promise<File[]> {
	if (hidden(entry.name)) return [];
	if (entry.isFile) {
		const file = entry as FileSystemFileEntry;
		return [await new Promise<File>((resolve, reject) => file.file(resolve, reject))];
	}
	const files: File[] = [];
	for (const child of await children(entry as FileSystemDirectoryEntry)) {
		files.push(...(await filesOf(child)));
	}
	return files;
}

/**
 * The files of a drop. Its entries are taken before anything waits, since the browser empties
 * the drop once its event is over. A browser that gives no entries gives its files as they are.
 */
export async function droppedFiles(data: DataTransfer): Promise<File[]> {
	const items = [...data.items].filter((item) => item.kind === 'file');
	const entries = items.map((item) => item.webkitGetAsEntry?.() ?? null);
	if (!entries.length || entries.includes(null)) return [...data.files];
	const files: File[] = [];
	for (const entry of entries as FileSystemEntry[]) files.push(...(await filesOf(entry)));
	return files;
}
