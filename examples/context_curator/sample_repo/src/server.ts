import express from "express";
import { ordersRouter } from "./api/orders";

export const app = express();
app.use(express.json());
app.use("/orders", ordersRouter);

if (require.main === module) {
  app.listen(3000, () => console.log("acme-orders listening on :3000"));
}
