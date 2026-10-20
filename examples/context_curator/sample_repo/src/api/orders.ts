import { Router } from "express";
import { z } from "zod";
import { findOrder } from "../db";

const Params = z.object({ id: z.string().uuid() });

export const ordersRouter = Router();

ordersRouter.get("/:id", async (req, res) => {
  const { id } = Params.parse(req.params);
  const order = await findOrder(id);
  if (!order) return res.status(404).json({ error: "not found" });
  res.json(order);
});
