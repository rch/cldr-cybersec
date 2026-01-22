package com.example.flink;

import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.table.data.GenericRowData;
import org.apache.flink.table.data.RowData;
import org.apache.flink.table.data.StringData;
import org.apache.iceberg.flink.TableLoader;
import org.apache.iceberg.flink.sink.FlinkSink;

import java.util.Random;

/**
 * Simple Flink DataGen to Iceberg streaming job
 * Generates test records continuously and writes to Iceberg table
 */
public class DataGenToIceberg {
    
    private static final Random RANDOM = new Random();
    
    public static void main(String[] args) throws Exception {
        // Create execution environment
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(1);
        
        // Generate test data stream (1 record every 3 seconds = rate of 0.333/sec)
        DataStream<RowData> testData = env.addSource(new TestDataGenerator())
            .name("test-data-generator");
        
        // Configure Iceberg table loader
        String warehousePath = "s3://cybersec/iceberg/warehouse";
        String catalogName = "cybersec";
        String tableName = "e2e_test.test_data";
        
        TableLoader tableLoader = TableLoader.fromCatalog(
            // Using REST catalog
            // Need to configure with proper credentials
            null, // Will need proper catalog implementation
            tableName
        );
        
        // Write to Iceberg
        FlinkSink.forRowData(testData)
            .tableLoader(tableLoader)
            .append();
        
        // Execute job
        env.execute("DataGen to Iceberg Streaming Job");
    }
    
    static class TestDataGenerator implements org.apache.flink.streaming.api.functions.source.SourceFunction<RowData> {
        private volatile boolean isRunning = true;
        private final Random random = new Random();
        private int count = 0;
        
        @Override
        public void run(SourceContext<RowData> ctx) throws Exception {
            while (isRunning) {
                // Generate one record
                GenericRowData row = new GenericRowData(4);
                row.setField(0, StringData.fromString(generateRandomString(10))); // id
                row.setField(1, StringData.fromString(generateRandomString(20))); // name
                row.setField(2, (long) (random.nextInt(1000) + 1)); // amount
                row.setField(3, StringData.fromString(String.valueOf(count % 5))); // region
                
                ctx.collect(row);
                count++;
                
                // Wait 3 seconds before generating next record
                Thread.sleep(3000);
            }
        }
        
        @Override
        public void cancel() {
            isRunning = false;
        }
        
        private String generateRandomString(int length) {
            String chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
            StringBuilder sb = new StringBuilder(length);
            for (int i = 0; i < length; i++) {
                sb.append(chars.charAt(random.nextInt(chars.length())));
            }
            return sb.toString();
        }
    }
}
