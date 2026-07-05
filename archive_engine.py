import os
import zipfile
import tarfile
import time
import logging

try:
    import py7zr
except ImportError:
    py7zr = None

class ArchiveEngine:
    """Движок архивации с поддержкой форматов ZIP, 7z, TAR.GZ и уровней сжатия."""
    
    def __init__(self, buffer_size=1024 * 1024 * 4):
        self.buffer_size = buffer_size
        self.is_cancelled = False

    def cancel_backup(self):
        self.is_cancelled = True

    def _make_safe_arcname(self, file_path):
        """Преобразует абсолютный путь в безопасный (C:\\... -> C_Drive/...)."""
        drive, tail = os.path.splitdrive(file_path)
        if drive:
            drive = drive.replace(":", "_Drive")
        tail = tail.lstrip("\\/")
        return os.path.join(drive, tail).replace("\\", "/")

    def _get_zip_compression(self, level_str):
        """Маппинг уровней сжатия для ZIP."""
        if level_str == "Без сжатия":
            return zipfile.ZIP_STORED, 0
        mapping = {"Быстрое": 1, "Обычное": 6, "Максимальное": 9}
        return zipfile.ZIP_DEFLATED, mapping.get(level_str, 6)

    def run_backup(self, dest_archive_path, files_list, comp_type="ZIP", comp_level="Обычное", global_progress_callback=None):
        self.is_cancelled = False
        if not files_list:
            logging.info("Nothing to archive.")
            return
            
        # FIX: Оптимизированный расчет размера файлов
        total_bytes = 0
        for f in files_list:
            if os.path.exists(f):
                try:
                    total_bytes += os.path.getsize(f)
                except OSError:
                    pass
        
        if total_bytes == 0:
            return

        global_copied_bytes = 0
        start_time = time.time()
        logging.info(f"Archive backup ({comp_type}, Уровень: {comp_level}) started: {dest_archive_path}")
        
        # FIX: Безопасная обработка пути (если dirname пустой, вернет "")
        dest_dir = os.path.dirname(dest_archive_path) or "."
        os.makedirs(dest_dir, exist_ok=True)

        try:
            if comp_type == "ZIP":
                method, level = self._get_zip_compression(comp_level)
                # FIX: compresslevel передается при создании ZipFile, не при открытии файла
                with zipfile.ZipFile(dest_archive_path, 'w', compression=method, compresslevel=level) as zf:
                    for file_path in files_list:
                        if self.is_cancelled: 
                            break
                        if not os.path.exists(file_path): 
                            continue
                        
                        arcname = self._make_safe_arcname(file_path)
                        try:
                            with open(file_path, 'rb') as src:
                                with zf.open(arcname, 'w') as dest:
                                    while True:
                                        if self.is_cancelled: 
                                            break
                                        chunk = src.read(self.buffer_size)
                                        if not chunk: 
                                            break
                                        dest.write(chunk)
                                        global_copied_bytes += len(chunk)
                                        if global_progress_callback:
                                            global_progress_callback(global_copied_bytes, total_bytes, file_path)
                        except (PermissionError, OSError) as e:
                            logging.warning(f"Пропуск файла {file_path}: {e}")

            elif comp_type == "TAR.GZ":
                t_level = {"Без сжатия": 0, "Быстрое": 1, "Обычное": 6, "Максимальное": 9}.get(comp_level, 6)
                mode = 'w' if t_level == 0 else 'w:gz'
                
                kwargs = {} if t_level == 0 else {"compresslevel": t_level}
                with tarfile.open(dest_archive_path, mode, **kwargs) as tf:
                    for file_path in files_list:
                        if self.is_cancelled: 
                            break
                        if not os.path.exists(file_path): 
                            continue
                        
                        arcname = self._make_safe_arcname(file_path)
                        try:
                            tar_info = tf.gettarinfo(file_path, arcname=arcname)
                            
                            # FIX: Использовать fileobj для потоковой записи содержимого файла
                            with open(file_path, 'rb') as src:
                                tf.addfile(tarinfo=tar_info, fileobj=src)
                            
                            file_size = os.path.getsize(file_path)
                            global_copied_bytes += file_size
                            if global_progress_callback:
                                global_progress_callback(global_copied_bytes, total_bytes, file_path)
                        except (PermissionError, OSError) as e:
                            logging.warning(f"Пропуск файла {file_path}: {e}")

            elif comp_type == "7z":
                if py7zr is None:
                    raise ImportError("Библиотека 'py7zr' не установлена! Выполните 'pip install py7zr'")
                
                with py7zr.SevenZipFile(dest_archive_path, 'w') as sz:
                    for file_path in files_list:
                        if self.is_cancelled: 
                            break
                        if not os.path.exists(file_path): 
                            continue
                        
                        arcname = self._make_safe_arcname(file_path)
                        try:
                            sz.write(file_path, arcname)
                            file_size = os.path.getsize(file_path)
                            global_copied_bytes += file_size
                            if global_progress_callback:
                                global_progress_callback(global_copied_bytes, total_bytes, file_path)
                        except (PermissionError, OSError) as e:
                            logging.warning(f"Пропуск файла {file_path}: {e}")

        except Exception as e:
            logging.error(f"Archive failed: {e}")
            if os.path.exists(dest_archive_path):
                try:
                    os.remove(dest_archive_path)
                except OSError:
                    pass
            return

        if self.is_cancelled:
            logging.warning("Backup cancelled. Removing partial archive...")
            if os.path.exists(dest_archive_path):
                try:
                    os.remove(dest_archive_path)
                except OSError:
                    pass
            return

        elapsed_time = time.time() - start_time
        logging.info(f"Archive complete in {elapsed_time:.2f} seconds")
