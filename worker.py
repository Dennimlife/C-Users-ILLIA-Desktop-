# worker.py
from PySide6.QtCore import QThread, Signal
import time
import os
import logging
from archive_engine import ArchiveEngine
from crypto_engine import CryptoEngine
from core_engine import FileScanner

class BackupWorker(QThread):
    progress_updated = Signal(object, object, str)
    log_message = Signal(str)
    file_processed = Signal(str)
    finished_backup = Signal(float)

    def __init__(self, sources, dest_dir, password=None, use_encryption=False, backup_type="Полная", comp_type="ZIP", comp_level="Обычное"):
        super().__init__()
        self.sources = sources if isinstance(sources, list) else [sources]
        self.dest_dir = dest_dir
        self.password = password
        self.use_encryption = use_encryption
        self.backup_type = backup_type
        self.comp_type = comp_type
        self.comp_level = comp_level
        
        self.archive_engine = ArchiveEngine()
        self.crypto_engine = CryptoEngine(self.password) if password else None
        self.is_cancelled = False

    def run(self):
        start_time = time.time()
        
        # FIX: Используем FileScanner для умного сканирования вместо простого os.walk
        file_list, total_bytes, current_state = FileScanner.scan_directories(
            self.sources, 
            self.dest_dir, 
            self.backup_type
        )
        
        if not file_list:
            self.log_message.emit("Внимание: Выбраны пустые папки или нет изменений. Нечего копировать.")
            self.finished_backup.emit(0.0)
            return

        def update_ui(copied_bytes, total_bytes, current_file):
            self.progress_updated.emit(copied_bytes, total_bytes, current_file)
            self.file_processed.emit(current_file)

        # FIX: Правильное имя архива с расширением
        zip_filename = f"backup_{int(time.time())}.zip"
        full_dest_path = os.path.join(self.dest_dir, zip_filename)
        
        self.log_message.emit(f"Архивация {len(file_list)} файлов ({total_bytes / (1024**2):.2f} МБ)...")
        
        # FIX: Правильный порядок параметров для run_backup
        # run_backup(dest_archive_path, files_list, comp_type, comp_level, callback)
        self.archive_engine.run_backup(
            dest_archive_path=full_dest_path,
            files_list=file_list,
            comp_type=self.comp_type,
            comp_level=self.comp_level,
            global_progress_callback=update_ui
        )

        # Проверяем, был ли отмена операции
        if self.archive_engine.is_cancelled:
            self.log_message.emit("Архивация отменена пользователем.")
            self.finished_backup.emit(time.time() - start_time)
            return

        # FIX: Проверяем создание архива перед шифрованием
        if self.use_encryption and self.crypto_engine:
            if os.path.exists(full_dest_path):
                self.log_message.emit("Шифрование архива (AES-256)...")
                enc_dest_path = full_dest_path + ".enc"
                self.crypto_engine.encrypt_file(full_dest_path, enc_dest_path, update_ui)
                
                # Если шифрование успешно, удаляем оригинальный файл
                if not self.crypto_engine.is_cancelled and os.path.exists(full_dest_path):
                    try:
                        os.remove(full_dest_path)
                        self.log_message.emit(f"Оригинальный архив удален. Зашифрованный файл: {enc_dest_path}")
                    except OSError as e:
                        self.log_message.emit(f"Предупреждение: Не удалось удалить оригинальный архив: {e}")
            else:
                self.log_message.emit("Ошибка: Архив не был создан. Шифрование прервано.")
        else:
            self.log_message.emit(f"Резервная копия создана: {full_dest_path}")
        
        # Сохраняем манифест для инкрементальных/дифференциальных бэкапов
        FileScanner.save_manifest(self.dest_dir, current_state)
        
        self.finished_backup.emit(time.time() - start_time)

    def stop(self):
        """Безопасно останавливает процесс бэкапа."""
        self.is_cancelled = True
        self.archive_engine.cancel_backup()
        if self.crypto_engine:
            self.crypto_engine.cancel()
