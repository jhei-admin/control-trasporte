from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flota_app", "0038_vehiculo_soporte_suspension"),
    ]

    operations = [
        migrations.CreateModel(
            name="EndpointUsageHourly",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("bucket_start", models.DateTimeField(db_index=True)),
                ("method", models.CharField(db_index=True, max_length=10)),
                ("path", models.CharField(db_index=True, max_length=255)),
                ("status_code", models.PositiveSmallIntegerField(db_index=True)),
                ("request_count", models.PositiveIntegerField(default=0)),
                ("bytes_sent", models.BigIntegerField(default=0)),
                ("latest_seen", models.DateTimeField(db_index=True)),
            ],
            options={
                "verbose_name": "Uso de endpoint por hora",
                "verbose_name_plural": "Uso de endpoints por hora",
                "ordering": ["-bucket_start", "-bytes_sent", "-request_count"],
            },
        ),
        migrations.AddIndex(
            model_name="endpointusagehourly",
            index=models.Index(fields=["bucket_start", "-bytes_sent"], name="flota_app_e_bucket__a21dc0_idx"),
        ),
        migrations.AddIndex(
            model_name="endpointusagehourly",
            index=models.Index(fields=["path", "bucket_start"], name="flota_app_e_path_352bbc_idx"),
        ),
        migrations.AddIndex(
            model_name="endpointusagehourly",
            index=models.Index(fields=["method", "path", "bucket_start"], name="flota_app_e_method_e9c4db_idx"),
        ),
        migrations.AddConstraint(
            model_name="endpointusagehourly",
            constraint=models.UniqueConstraint(fields=("bucket_start", "method", "path", "status_code"), name="unique_endpoint_usage_hourly"),
        ),
    ]
