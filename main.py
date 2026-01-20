"""
Cybersec Toolkit - CloudTrail Event Pipeline Orchestrator

This orchestrates the complete pipeline:
1. Flink DataGen -> Generate CloudTrail events
2. Flink Processor -> Parse and enrich events
3. PyIceberg Writer -> Persist to Iceberg format with PostgreSQL catalog
"""

import subprocess
import sys
import time
import os
from pathlib import Path


class PipelineOrchestrator:
    """Orchestrate the CloudTrail event processing pipeline"""
    
    def __init__(self):
        self.base_dir = Path(__file__).parent
        self.processes = {}
    
    def check_services(self):
        """Check if required services are running"""
        print("Checking required services...")
        
        services = {
            "PostgreSQL": ("localhost", 5438),
            "MinIO": ("localhost", 9010),
            "Flink JobManager": ("localhost", 8081),
        }
        
        import socket
        for service, (host, port) in services.items():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex((host, port))
                sock.close()
                
                if result == 0:
                    print(f"  ✓ {service} is running on {host}:{port}")
                else:
                    print(f"  ✗ {service} is NOT running on {host}:{port}")
                    print(f"    Run 'devenv up' to start services")
                    return False
            except Exception as e:
                print(f"  ✗ Error checking {service}: {e}")
                return False
        
        return True
    
    def initialize_iceberg_catalog(self):
        """Initialize Iceberg catalog in PostgreSQL"""
        print("\nInitializing Iceberg catalog...")
        try:
            subprocess.run(
                ["devenv", "run", "init-iceberg"],
                check=True,
                capture_output=True,
                text=True
            )
            print("  ✓ Iceberg catalog initialized")
        except subprocess.CalledProcessError as e:
            print(f"  ! Iceberg catalog initialization failed (may already exist): {e}")
    
    def start_datagen(self):
        """Start the CloudTrail data generator"""
        print("\nStarting CloudTrail DataGen...")
        
        # For PyFlink, we need to submit to the Flink cluster
        flink_home = os.getenv("FLINK_HOME", "/opt/flink")
        datagen_script = self.base_dir / "flink_jobs" / "cloudtrail_datagen.py"
        
        print(f"  Submitting DataGen job to Flink cluster...")
        print(f"  Job script: {datagen_script}")
        print(f"  Note: Make sure Flink cluster is running (devenv up)")
        
        # Instructions for manual submission
        print("\n  To submit the job manually:")
        print(f"  python {datagen_script}")
        
    def start_processor(self):
        """Start the CloudTrail processor"""
        print("\nStarting CloudTrail Processor...")
        
        processor_script = self.base_dir / "flink_jobs" / "cloudtrail_processor.py"
        
        print(f"  Submitting Processor job to Flink cluster...")
        print(f"  Job script: {processor_script}")
        
        # Instructions for manual submission
        print("\n  To submit the job manually:")
        print(f"  python {processor_script}")
    
    def start_iceberg_writer(self):
        """Start the Iceberg writer"""
        print("\nStarting Iceberg Writer...")
        
        writer_script = self.base_dir / "iceberg_writer" / "cloudtrail_writer.py"
        
        # Set environment variables
        env = os.environ.copy()
        env.update({
            "ICEBERG_CATALOG_URI": "postgresql://postgres@localhost:5438/cybersec",
            "ICEBERG_WAREHOUSE": "s3://cybersec/iceberg/warehouse",
            "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
            "AWS_ACCESS_KEY_ID": "minioadmin",
            "AWS_SECRET_ACCESS_KEY": "minioadmin",
            "S3_ENDPOINT": "http://localhost:9010"
        })
        
        print(f"  Starting writer: {writer_script}")
        proc = subprocess.Popen(
            [sys.executable, str(writer_script)],
            env=env
        )
        self.processes['iceberg_writer'] = proc
        print(f"  ✓ Iceberg writer started (PID: {proc.pid})")
    
    def show_status(self):
        """Show pipeline status and useful URLs"""
        print("\n" + "=" * 60)
        print("CloudTrail Event Processing Pipeline")
        print("=" * 60)
        print("\n📊 Service URLs:")
        print("  • Flink Dashboard:  http://localhost:8081")
        print("  • MinIO Console:    http://localhost:9011")
        print("  • PostgreSQL:       localhost:5438 (user: postgres, db: cybersec)")
        
        print("\n📈 Pipeline Components:")
        print("  1. CloudTrail DataGen (Flink) - Generates synthetic events")
        print("  2. CloudTrail Processor (Flink) - Parses and enriches events")
        print("  3. Iceberg Writer (Python) - Persists to Iceberg format")
        
        print("\n🔍 Query Examples:")
        print("  python iceberg_writer/cloudtrail_query.py")
        
        print("\n⚙️  Configuration:")
        print("  • Iceberg Catalog: PostgreSQL (localhost:5438/cybersec)")
        print("  • Iceberg Warehouse: s3://cybersec/iceberg/warehouse (MinIO)")
        print("  • Kafka Topics: cloudtrail-raw, cloudtrail-parsed")
        
        print("\n" + "=" * 60)
    
    def run_interactive(self):
        """Run pipeline in interactive mode"""
        print("\n🚀 Cybersec CloudTrail Pipeline")
        print("=" * 60)
        
        if not self.check_services():
            print("\n⚠️  Required services are not running!")
            print("Please run 'devenv up' in a separate terminal first.")
            return
        
        self.initialize_iceberg_catalog()
        self.show_status()
        
        print("\n📝 Next Steps:")
        print("  1. Start CloudTrail DataGen:")
        print("     python flink_jobs/cloudtrail_datagen.py")
        print("\n  2. Start CloudTrail Processor:")
        print("     python flink_jobs/cloudtrail_processor.py")
        print("\n  3. Start Iceberg Writer:")
        print("     python iceberg_writer/cloudtrail_writer.py")
        print("\n  4. Query the data:")
        print("     python iceberg_writer/cloudtrail_query.py")
        
        print("\n💡 Tip: Each component can be started in a separate terminal")
        print("    or submitted to the Flink cluster via the web UI\n")


def main():
    """Main entry point"""
    orchestrator = PipelineOrchestrator()
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "check":
            orchestrator.check_services()
        elif command == "init":
            orchestrator.initialize_iceberg_catalog()
        elif command == "status":
            orchestrator.show_status()
        elif command == "writer":
            orchestrator.start_iceberg_writer()
        else:
            print(f"Unknown command: {command}")
            print("Available commands: check, init, status, writer")
    else:
        orchestrator.run_interactive()


if __name__ == "__main__":
    main()

