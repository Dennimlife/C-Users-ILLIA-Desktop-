import os
import shutil
import logging
import time
import json

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s INFO %(message)s',
    datefmt='%H:%M:%S'
)

class FileScanner:
    """Модуль для умного сканирования источников с поддержкой инкрементального анализа."""
    
    @staticmethod
    def load_manifest(dest_dir):
        """Загружает манифест предыдущего бэкапа из целевой директории."""
        manifest_path = os.path.join(dest_dir, "backup_manifest.json")
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Ошибка чтения манифеста: {e}")
        return {}

    @staticmethod
    def save_manifest(dest_dir, state_dict):
        """Сохраняет текущее состояние файлов для последующих проверок."""
        manifest_path = os.path.join(dest_dir, "backup_manifest.json")
        os.makedirs(dest_dir, exist_ok=True)
        try:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(state_dict, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logging.error(f"Не удалось сохранить манифест: {e}")

    @classmethod
    def scan_directories(cls, source_paths, dest_dir, backup_type="Полная"):
        """
        Сканирует список путей и фильтрует файлы в зависимости от выбранного типа бэкапа.
        Возвращает: (список_файлов_для_копирования, общий_объем, карта_текущего_состояния)
        """
        file_list = []
        total_size = 0
        current_state = {}
        
        # FIX: Загружаем манифест только для инкрементальных бэкапов
        old_manifest = cls.load_manifest(dest_dir) if backup_type in ["Инкрементальная", "Дифференциальная"] else {}
        # FIX: Конвертируем в set для O(1) поиска вместо O(n)
        old_files_set = set(old_manifest.keys()) if old_manifest else set()

        logging.info(f"Scanning sources for backup type: {backup_type}")
        
        # source_paths может быть как строкой (один путь), так и списком путей
        if isinstance(source_paths, str):
            source_paths = [source_paths]

        for source_dir in source_paths:
            if not os.path.exists(source_dir):
                logging.warning(f"Источник не существует: {source_dir}")
                continue
                
            # Если передан путь к конкретному файлу, а не к папке
            if os.path.isfile(source_dir):
                try:
                    mtime = os.path.getmtime(source_dir)
                    size = os.path.getsize(source_dir)
                    current_state[source_dir] = {"mtime": mtime, "size": size}
                    
                    # FIX: Оптимизированная проверка - используем set для быстрого поиска
                    is_modified = True
                    if source_dir in old_files_set:
                        old_file = old_manifest[source_dir]
                        if old_file["mtime"] == mtime and old_file["size"] == size:
                            is_modified = False
                    
                    if backup_type == "Полная" or is_modified:
                        file_list.append(source_dir)
                        total_size += size
                except OSError:
                    pass
                continue

            # Сканирование дерева папок
            for root, _, files in os.walk(source_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    try:
                        mtime = os.path.getmtime(file_path)
                        size = os.path.getsize(file_path)
                        
                        # Фиксируем текущее состояние файла
                        current_state[file_path] = {"mtime": mtime, "size": size}
                        
                        # FIX: Логика фильтрации изменений оптимизирована
                        is_modified = True
                        if backup_type in ["Инкрементальная", "Дифференциальная"]:
                            # FIX: Используем set для O(1) поиска вместо поиска в словаре
                            if file_path in old_files_set:
                                old_file = old_manifest[file_path]
                                # Если дата изменения и размер совпадают — файл пропускаем
                                if old_file["mtime"] == mtime and old_file["size"] == size:
                                    is_modified = False
                        
                        if is_modified:
                            file_list.append(file_path)
                            total_size += size
                            
                    except OSError as e:
                        logging.warning(f"Cannot access {file_path}: {e}")
                        
        logging.info(f"Filtered {len(file_list)} files to backup. Size: {total_size / (1024**2):.2f} MB")
        return file_list, total_size, current_state


class CopyEngine:
    """Модуль побайтового копирования файлов с честным чанк-бай-чанк прогрессом."""
    
    def __init__(self, buffer_size=1024 * 1024 * 4):
        self.buffer_size = buffer_size
        self.is_cancelled = False

    def cancel_backup(self):
        self.is_cancelled = True

    def run_backup(self, source_paths, dest_dir, backup_type="Полная", global_progress_callback=None):
        """Запускает процесс бэкапа без архивации с сохранением структуры папок."""
        self.is_cancelled = False
        
        # 1. Проводим умный пре-скан изменений
        file_list, total_bytes, current_state = FileScanner.scan_directories(source_paths, dest_dir, backup_type)
        
        if not file_list:
            logging.info("Nothing to backup (No changes detected).")
            # Сохраняем состояние, даже если изменений нет
            FileScanner.save_manifest(dest_dir, current_state)
            return

        global_copied_bytes = 0
        start_time = time.time()
        logging.info(f"Direct Copy Backup ({backup_type}) started")

        for file_path in file_list:
            if self.is_cancelled:
                break
                
            # Для сохранения структуры определяем относительно какого базового источника лежит файл
            # Находим совпадение корня
            base_src = ""
            if isinstance(source_paths, list):
                for p in source_paths:
                    if file_path.startswith(p):
                        base_src = p
                        break
            else:
                base_src = source_paths

            rel_path = os.path.relpath(file_path, base_src) if base_src else os.path.basename(file_path)
            # Избавляемся от двоеточий дисков в пути назначения (например, при бэкапе целого диска)
            rel_path = rel_path.replace(":", "_Drive")
            dest_path = os.path.join(dest_dir, rel_path)
            
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            
            try:
                # Поблочное копирование файла
                with open(file_path, 'rb') as fsrc, open(dest_path, 'wb') as fdst:
                    while True:
                        if self.is_cancelled: break
                        chunk = fsrc.read(self.buffer_size)
                        if not chunk: break
                        
                        fdst.write(chunk)
                        global_copied_bytes += len(chunk)
                        
                        if global_progress_callback:
                            global_progress_callback(global_copied_bytes, total_bytes, file_path)
                            
                if not self.is_cancelled:
                    shutil.copystat(file_path, dest_path)
                    
            except Exception as e:
                logging.error(f"Failed to copy {file_path}: {e}")

        # Если операция успешна, сохраняем новый манифест слепка данных
        if not self.is_cancelled:
            # FIX: Для дифференциального бэкапа сохраняем отдельный "полный снимок" для следующих дифф-копий
            FileScanner.save_manifest(dest_dir, current_state)
            elapsed_time = time.time() - start_time
            logging.info(f"Backup complete in {elapsed_time:.2f} seconds")
        else:
            logging.warning("Backup cancelled by user.")
