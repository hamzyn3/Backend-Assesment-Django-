from django.db import models


class FuelStation(models.Model):
    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=200)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=7, decimal_places=4)  # USD per gallon
    latitude = models.FloatField()
    longitude = models.FloatField()

    class Meta:
        indexes = [models.Index(fields=["latitude", "longitude"])]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.price}"
